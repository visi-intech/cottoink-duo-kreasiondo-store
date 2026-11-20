/** @odoo-module */

// Integrasi PoS (Odoo 17) dengan modul stock_no_negative.
//
//  - Produk ber-kontrol stok (storable + negative stock tidak diizinkan) yang
//    stoknya habis TETAP TAMPIL di product card, tapi pudar + label
//    "Stok Habis" dan tidak bisa ditambahkan ke order.
//  - Penambahan produk (klik card, scan barcode, popup pilih barcode) ditolak
//    jika total qty di order melebihi stok.
//  - Menaikkan qty line (numpad / scan ulang produk yang sama) juga dicek.
//  - Tombol Validate di Payment ditolak jika ada produk yang qty-nya melebihi
//    stok (dicek LIVE ke server).
//  - Order refund / qty negatif tidak diblokir.
//
// Data stok (vit_pos_qty, vit_pos_stock_control) dikirim server saat produk
// dimuat (lihat models/pos_stock_no_negative.py) dan di-refresh: saat klik,
// saat validasi, setelah order tersinkron, dan berkala.

import { patch } from "@web/core/utils/patch";
import { _t } from "@web/core/l10n/translation";
import { parseFloat as oParseFloat } from "@web/views/fields/parsers";
import { Product, Order, Orderline } from "@point_of_sale/app/store/models";
import { PosStore } from "@point_of_sale/app/store/pos_store";
import { PaymentScreen } from "@point_of_sale/app/screens/payment_screen/payment_screen";
import { ProductCard } from "@point_of_sale/app/generic_components/product_card/product_card";
import { usePos } from "@point_of_sale/app/store/pos_hook";
import { ErrorPopup } from "@point_of_sale/app/errors/popups/error_popup";

const EPS = 0.000001;
const REFRESH_INTERVAL_MS = 5 * 60 * 1000; // refresh stok produk ber-kontrol tiap 5 menit
const REFRESH_CHUNK = 1000; // jumlah produk per panggilan ke server

/** Total qty (positif) produk di order, opsional mengecualikan satu line. */
function qtyInOrder(order, productId, excludeLine = null) {
    let total = 0;
    for (const line of order?.get_orderlines?.() || []) {
        if (excludeLine && line.id === excludeLine.id) {
            continue;
        }
        if (line.product?.id === productId) {
            total += line.get_quantity();
        }
    }
    return total;
}

// ---- Product: status stok habis ----
patch(Product.prototype, {
    get vitIsOutOfStock() {
        return !!this.vit_pos_stock_control && (this.vit_pos_qty || 0) <= EPS;
    },
});

// ---- Store: refresh & cek stok ----
patch(PosStore.prototype, {
    async after_load_server_data() {
        const res = await super.after_load_server_data(...arguments);
        if (!this._vitStockTimer) {
            this._vitStockTimer = setInterval(() => this.vitRefreshStock(), REFRESH_INTERVAL_MS);
        }
        return res;
    },

    /**
     * Ambil stok terbaru dari server.
     *  - productIds diisi : hanya produk tersebut
     *  - tanpa productIds : semua produk ber-kontrol stok (timer berkala)
     * Jika gagal (offline), data lokal tetap dipakai.
     */
    async vitRefreshStock(productIds) {
        const bulk = !productIds;
        if (bulk) {
            if (this._vitStockBusy) {
                return;
            }
            this._vitStockBusy = true;
        }
        try {
            const ids =
                productIds ||
                Object.values(this.db.product_by_id)
                    .filter((p) => p.vit_pos_stock_control)
                    .map((p) => p.id);
            for (let i = 0; i < ids.length; i += REFRESH_CHUNK) {
                const result = await this.orm.silent.call("product.product", "vit_get_pos_stock", [
                    this.config.id,
                    ids.slice(i, i + REFRESH_CHUNK),
                ]);
                for (const [id, vals] of Object.entries(result)) {
                    const product = this.db.get_product_by_id(Number(id));
                    if (product) {
                        product.vit_pos_qty = vals.qty;
                        product.vit_pos_stock_control = vals.control;
                    }
                }
            }
        } catch (error) {
            console.warn("Gagal refresh stok PoS, memakai data lokal", error);
        } finally {
            if (bulk) {
                this._vitStockBusy = false;
            }
        }
    },

    vitShowStockError(product, available) {
        this.popup.add(ErrorPopup, {
            title: _t("Stok Tidak Cukup"),
            body:
                available > EPS
                    ? _t('Stok "%s" hanya tersisa %s.', product.display_name, this.env.utils.formatProductQty(available))
                    : _t('Stok "%s" habis, produk tidak bisa ditambahkan.', product.display_name),
        });
    },

    /** true = boleh ditambah qtyToAdd ke order; false = stok kurang (popup sudah tampil). */
    async vitCheckStock(product, qtyToAdd, order) {
        if (!product) {
            return true;
        }
        await this.vitRefreshStock([product.id]);
        if (!product.vit_pos_stock_control) {
            return true;
        }
        const available = product.vit_pos_qty || 0;
        if (qtyInOrder(order, product.id) + qtyToAdd > available + EPS) {
            this.vitShowStockError(product, available);
            return false;
        }
        return true;
    },

    /** Dipakai saat Validate di Payment: cek semua line ke server. */
    async vitValidateOrderStock(order) {
        const needed = new Map();
        for (const line of order.get_orderlines()) {
            const product = line.get_product();
            needed.set(product.id, (needed.get(product.id) || 0) + line.get_quantity());
        }
        const ids = [...needed].filter(([, qty]) => qty > EPS).map(([id]) => id);
        if (!ids.length) {
            return true;
        }
        await this.vitRefreshStock(ids);
        const problems = [];
        for (const id of ids) {
            const product = this.db.get_product_by_id(id);
            if (!product?.vit_pos_stock_control) {
                continue;
            }
            const available = product.vit_pos_qty || 0;
            if (needed.get(id) > available + EPS) {
                const fmt = this.env.utils.formatProductQty;
                problems.push(
                    _t("%s (dibutuhkan %s, tersedia %s)",product.display_name, fmt(needed.get(id)), fmt(Math.max(available, 0)))
                );
            }
        }
        if (problems.length) {
            this.popup.add(ErrorPopup, {
                title: _t("Stok Tidak Cukup"),
                body: _t("Transaksi tidak bisa divalidasi karena stok tidak cukup: ") + problems.join("; "),
            });
            return false;
        }
        return true;
    },

    // Klik product card: produk stok habis tidak bisa ditambahkan
    // (dicek sebelum popup varian/lot/combo muncul).
    async addProductToCurrentOrder(product, options = {}) {
        const p = Number.isInteger(product) ? this.db.get_product_by_id(product) : product;
        const order = this.get_order();
        if (p?.vitIsOutOfStock && !order?._isRefundOrder()) {
            await this.vitRefreshStock([p.id]); // bisa saja sudah di-restock
            if (p.vitIsOutOfStock) {
                this.vitShowStockError(p, 0);
                return;
            }
        }
        return super.addProductToCurrentOrder(...arguments);
    },

    // Setelah order tersinkron, stok berubah -> refresh produk yang terjual.
    _after_flush_orders(orders) {
        const res = super._after_flush_orders(...arguments);
        const ids = new Set();
        for (const order of orders || []) {
            for (const cmd of order?.data?.lines || []) {
                const productId = cmd?.[2]?.product_id;
                if (productId && this.db.get_product_by_id(productId)?.vit_pos_stock_control) {
                    ids.add(productId);
                }
            }
        }
        if (ids.size) {
            this.vitRefreshStock([...ids]);
        }
        return res;
    },
});

// ---- Order: semua jalur penambahan produk (klik, scan barcode, dll) ----
patch(Order.prototype, {
    async add_product(product, options) {
        const qty = options?.quantity ?? 1;
        if (qty > 0 && !this._isRefundOrder() && !this.finalized) {
            if (!(await this.pos.vitCheckStock(product, qty, this))) {
                return;
            }
        }
        return super.add_product(...arguments);
    },
});

// ---- Orderline: menaikkan qty (numpad, scan ulang, merge) ----
patch(Orderline.prototype, {
    set_quantity(quantity, keep_price) {
        const product = this.product;
        // Hanya line yang SUDAH ada di order (bukan saat line dibuat / order
        // di-restore dari local storage), dan bukan line refund.
        if (
            product?.vit_pos_stock_control &&
            !this.refunded_orderline_id &&
            this.order &&
            !this.order.finalized &&
            this.order.get_orderlines().some((l) => l.id === this.id)
        ) {
            let quant = quantity;
            if (typeof quant !== "number") {
                try {
                    quant = oParseFloat("" + (quant ? quant : 0));
                } catch {
                    quant = NaN;
                }
            }
            if (!isNaN(quant) && quant > this.quantity) {
                const available = product.vit_pos_qty || 0;
                if (qtyInOrder(this.order, product.id, this) + quant > available + EPS) {
                    this.pos.vitShowStockError(product, available);
                    return false;
                }
            }
        }
        return super.set_quantity(...arguments);
    },
});

// ---- Payment: Validate ditolak jika stok tidak cukup ----
patch(PaymentScreen.prototype, {
    async _isOrderValid(isForceValidate) {
        if (!(await this.pos.vitValidateOrderStock(this.currentOrder))) {
            return false;
        }
        return super._isOrderValid(...arguments);
    },
});

// ---- Product card: status stok habis untuk template ----
patch(ProductCard.prototype, {
    setup() {
        super.setup(...arguments);
        this.vitPos = usePos();
    },
    get vitIsOutOfStock() {
        return !!this.vitPos.db.get_product_by_id(this.props.productId)?.vitIsOutOfStock;
    },
});
