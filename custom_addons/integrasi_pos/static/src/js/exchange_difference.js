/** @odoo-module **/

import { patch } from "@web/core/utils/patch";
import { Order } from "@point_of_sale/app/store/models";
import { PaymentScreen } from "@point_of_sale/app/screens/payment_screen/payment_screen";
import { ErrorPopup } from "@point_of_sale/app/errors/popups/error_popup";
import { roundPrecision as round_pr } from "@web/core/utils/numbers";
import { _t } from "@web/core/l10n/translation";

patch(Order.prototype, {
    _getExchangeDiffProductId() {
        const ref = this.pos.config.exchange_difference_product_id;
        return (Array.isArray(ref) ? ref[0] : ref) || false;
    },

    _isExchangeDiffLine(line) {
        const pid = this._getExchangeDiffProductId();
        return !!pid && line.get_product().id === pid && !line.refunded_orderline_id;
    },

    /**
     * Idempotent: hapus baris pengselisih lama, hitung ulang, lalu tambahkan
     * lagi bila tukar barang menghasilkan total negatif.
     * Return { ok: true } atau { ok: false, reason }.
     */
    async syncExchangeDifference() {
        const diffProductId = this._getExchangeDiffProductId();

        // 1. Bersihkan baris pengselisih dari perhitungan sebelumnya
        if (diffProductId) {
            const oldLines = this.get_orderlines().filter((l) => this._isExchangeDiffLine(l));
            for (const line of oldLines) {
                this.removeOrderline(line);
            }
        }

        // 2. Hanya berlaku untuk tukar barang (ada retur DAN ada barang baru)
        const lines = this.get_orderlines();
        const hasReturn = lines.some((l) => l.get_quantity() < 0);
        const hasNewItem = lines.some((l) => l.get_quantity() > 0);
        if (!hasReturn || !hasNewItem) {
            return { ok: true };
        }

        // 3. Hanya jika barang baru LEBIH MURAH (total negatif)
        const rounding = this.pos.currency.rounding;
        const total = this.get_total_with_tax();
        if (total > -rounding / 2) {
            return { ok: true }; // total >= 0: customer bayar kekurangan / pas
        }

        if (!diffProductId) {
            return { ok: false, reason: "no_product" };
        }
        const product = this.pos.db.get_product_by_id(diffProductId);
        if (!product) {
            return { ok: false, reason: "not_loaded" };
        }

        // 4. Tambahkan item pengselisih sebesar selisih (positif) -> total = 0
        const gap = round_pr(-total, rounding);
        await this.add_product(product, { quantity: 1, price: gap, merge: false });

        // Fallback bila produk pengselisih terkena pajak: skala ulang harga
        const diffLine = this.get_last_orderline();
        const residual = this.get_total_with_tax();
        if (diffLine && Math.abs(residual) > rounding / 2) {
            const withTax = diffLine.get_price_with_tax();
            if (withTax) {
                diffLine.set_unit_price(round_pr((gap * gap) / withTax, rounding));
            }
        }
        return { ok: true };
    },
});

patch(PaymentScreen.prototype, {
    async _applyExchangeDifference() {
        const res = await this.currentOrder.syncExchangeDifference();
        if (!res.ok) {
            await this.popup.add(ErrorPopup, {
                title: _t("Produk Pengselisih Belum Diatur"),
                body:
                    res.reason === "not_loaded"
                        ? _t("Produk pengselisih tidak ter-load di POS. Pastikan 'Available in POS' aktif lalu muat ulang POS.")
                        : _t("Tukar barang ini lebih murah dari barang retur. Isi 'Produk Pengselisih Tukar Barang' di konfigurasi POS."),
            });
            return false;
        }
        return true;
    },

    onMounted() {
        super.onMounted(...arguments);
        this._applyExchangeDifference();
    },

    async validateOrder(isForceValidate) {
        // Pengaman: hitung ulang tepat sebelum validasi
        if (!(await this._applyExchangeDifference())) {
            return;
        }
        return super.validateOrder(...arguments);
    },
});