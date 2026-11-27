/** @odoo-module **/

/**
 * Popup pilih reward (promo / voucher) setelah kasir menambah produk (klik atau scan barcode).
 *
 * Hanya untuk reward diskon dengan Discount Applicability = "Order" (diskon seluruh order).
 * Reward lain (specific / cheapest / free product) tidak diubah.
 *
 * Aktif kalau setting "Reward Popup on Add Product" (pos.vit_reward_popup_on_add) dicentang.
 * Kalau tidak dicentang, tidak ada perubahan perilaku sama sekali.
 *
 * Cara kerja:
 *  1. Reward diskon "order" TIDAK lagi di-auto-claim oleh _updateRewards (difilter di
 *     getClaimableRewards saat auto=true), supaya kasir yang memilih.
 *  2. Setelah produk benar-benar masuk, order ditandai "perlu prompt" lalu _updateRewards
 *     dijalankan. Di hook _updateRewardLines (dipanggil setelah poin program terhitung)
 *     popup pilihan reward ditampilkan di luar mutex agar tidak memblokir update reward.
 *  3. Reward yang dipilih diterapkan lewat _applyReward. Pilihan "Tanpa promo" tidak
 *     menampilkan popup lagi sampai daftar reward yang memenuhi syarat berubah.
 *
 * File ini harus dimuat SETELAH reward_pos_override.js (override getClaimableRewards di sana
 * tidak memanggil super, jadi filter di bawah harus berada di atasnya).
 */

import { patch } from "@web/core/utils/patch";
import { Order } from "@point_of_sale/app/store/models";
import { ProductScreen } from "@point_of_sale/app/screens/product_screen/product_screen";
import { SelectionPopup } from "@point_of_sale/app/utils/input_popups/selection_popup";
import { _t } from "@web/core/l10n/translation";

// Penanda "perlu prompt" kedaluwarsa kalau tidak terpakai (mis. produk ditolak cek stok),
// supaya tidak muncul popup nyasar di perubahan order berikutnya.
const PROMPT_WINDOW_MS = 8000;
const MAX_PROMPT_ROUNDS = 3;

function isOrderDiscountReward(reward) {
    return (
        !!reward &&
        reward.reward_type === "discount" &&
        reward.discount_applicability === "order" &&
        // ewallet itu pembayaran dari saldo, tetap diterapkan otomatis seperti bawaan Odoo
        reward.program_id?.program_type !== "ewallet"
    );
}

patch(Order.prototype, {
    _vitRewardPopupEnabled() {
        return !!this.pos?.config?.vit_reward_popup_on_add;
    },

    /**
     * Dipanggil SETELAH produk benar-benar masuk ke order (qty produk biasa bertambah).
     * Tanda dipasang dulu, baru _updateRewards dijalankan, supaya pasti ada satu putaran
     * _updateRewards yang melihat tanda itu (tidak bergantung urutan task async).
     */
    _vitAfterProductAdded(qtyBefore) {
        if (!this._vitRewardPopupEnabled() || this.finalized || !this.pos.programs?.length) {
            return;
        }
        if (this._vitRegularQty() <= qtyBefore) {
            return; // produk tidak jadi masuk (mis. stok habis): tidak ada popup
        }
        this._vitRewardPromptUntil = Date.now() + PROMPT_WINDOW_MS;
        this._updateRewards();
    },

    async add_product(product, options) {
        // Hanya penambahan produk biasa: bukan refund, bukan baris diskon/reward buatan sistem.
        const qty = options?.quantity ?? 1;
        const price = options?.price;
        const isSystemLine = options?.extras?.is_reward_line || options?.extras?.is_manual_discount;
        const isRefund = typeof this._isRefundOrder === "function" && this._isRefundOrder();
        if (!(qty > 0 && !(price < 0) && !isSystemLine && !isRefund)) {
            return super.add_product(...arguments);
        }
        const qtyBefore = this._vitRegularQty();
        const res = await super.add_product(...arguments);
        this._vitAfterProductAdded(qtyBefore);
        return res;
    },

    _vitRegularQty() {
        return this.orderlines
            .filter((line) => !line.is_reward_line)
            .reduce((sum, line) => sum + line.get_quantity(), 0);
    },

    getClaimableRewards(coupon_id = false, program_id = false, auto = false) {
        const result = super.getClaimableRewards(...arguments);
        if (auto && this._vitRewardPopupEnabled()) {
            // auto=true hanya dipakai _updateRewards untuk auto-claim: sisihkan diskon "order"
            // supaya dipilih kasir lewat popup.
            return result.filter(({ reward }) => !isOrderDiscountReward(reward));
        }
        return result;
    },

    _updateRewardLines() {
        const res = super._updateRewardLines(...arguments);
        // Dipanggil di dalam task _updateRewards, setelah poin program terhitung.
        if (this._vitRewardPromptUntil) {
            const valid = Date.now() <= this._vitRewardPromptUntil;
            this._vitRewardPromptUntil = 0;
            if (valid && this._vitRewardPopupEnabled()) {
                // di luar task mutex: popup menunggu input kasir, tidak boleh memblokir update reward
                setTimeout(() => this._vitPromptReward(), 0);
            }
        }
        return res;
    },

    _vitGetRewardCandidates() {
        const applied = new Set(this._get_reward_lines().map((line) => line.reward_id));
        const seen = new Set();
        const candidates = [];
        for (const candidate of this.getClaimableRewards()) {
            const { reward, coupon_id } = candidate;
            if (!isOrderDiscountReward(reward)) {
                continue;
            }
            if (applied.has(reward.id) || this.disabledRewards?.has(reward.id)) {
                continue;
            }
            const key = `${coupon_id}:${reward.id}`;
            if (seen.has(key)) {
                continue;
            }
            seen.add(key);
            candidates.push(candidate);
        }
        return candidates;
    },

    _vitRewardKey(candidates) {
        return candidates
            .map(({ reward, coupon_id }) => `${coupon_id}:${reward.id}`)
            .sort()
            .join("|");
    },

    async _vitPromptReward() {
        if (this._vitPromptBusy) {
            this._vitPromptAgain = true; // evaluasi ulang setelah popup yang sedang tampil selesai
            return;
        }
        this._vitPromptBusy = true;
        try {
            let rounds = 0;
            do {
                this._vitPromptAgain = false;
                await this._vitPromptRewardOnce();
            } while (this._vitPromptAgain && ++rounds < MAX_PROMPT_ROUNDS);
        } catch (error) {
            console.error("Reward popup gagal:", error);
        } finally {
            this._vitPromptBusy = false;
        }
    },

    async _vitPromptRewardOnce() {
        const stillValid = () =>
            !this.finalized && this.pos.get_order() === this && this._vitRewardPopupEnabled();
        if (!stillValid()) {
            return;
        }
        const candidates = this._vitGetRewardCandidates();
        if (!candidates.length) {
            this._vitDeclinedRewardKey = "";
            return;
        }
        const key = this._vitRewardKey(candidates);
        if (key === this._vitDeclinedRewardKey) {
            return; // daftar sama dengan yang sudah ditawarkan / dilewati kasir
        }

        const list = candidates.map((candidate) => ({
            id: `${candidate.coupon_id}:${candidate.reward.id}`,
            label: candidate.reward.description,
            description: candidate.reward.program_id?.name || "",
            item: candidate,
        }));
        const { confirmed, payload } = await this.env.services.popup.add(SelectionPopup, {
            title: _t("Pilih promo / voucher"),
            list,
            cancelText: _t("Tanpa promo"),
        });
        // apa pun pilihannya, daftar yang sama tidak ditawarkan lagi sampai berubah
        this._vitDeclinedRewardKey = key;
        if (!confirmed || !payload || !stillValid()) {
            return;
        }

        const result = this._applyReward(payload.reward, payload.coupon_id);
        if (result !== true) {
            this.env.services.pos_notification.add(result, 5000);
            return;
        }
        this._updateRewards();
        this._vitDeclinedRewardKey = this._vitRewardKey(this._vitGetRewardCandidates());
    },
});

// Scan barcode: jalur "tambah qty ke baris yang sudah ada" (integrasi_pos/_addScannedProduct)
// tidak melewati add_product, jadi dicek juga di sini. Kalau lewat add_product, tanda sudah
// terpasang dan putaran kedua tidak menampilkan popup lagi (daftar reward sama -> dilewati).
patch(ProductScreen.prototype, {
    async _barcodeProductAction() {
        const order = this.currentOrder;
        const qtyBefore = order?._vitRegularQty?.() ?? 0;
        const res = await super._barcodeProductAction(...arguments);
        order?._vitAfterProductAdded?.(qtyBefore);
        return res;
    },

    async _barcodeGS1Action() {
        const order = this.currentOrder;
        const qtyBefore = order?._vitRegularQty?.() ?? 0;
        const res = await super._barcodeGS1Action(...arguments);
        order?._vitAfterProductAdded?.(qtyBefore);
        return res;
    },
});
