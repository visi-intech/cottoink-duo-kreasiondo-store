/** @odoo-module **/

import { patch } from "@web/core/utils/patch";
import { Order } from "@point_of_sale/app/store/models";
import { _t } from "@web/core/l10n/translation";
import { ConfirmPopup } from "@point_of_sale/app/utils/confirm_popup/confirm_popup";

// ========== PATCH ORDER: FIX COUPON ACTIVATION ==========
patch(Order.prototype, {
    /**
     * 🔑 OVERRIDE: Cek apakah coupon sudah diaktivasi di order SAAT INI
     * Izinkan re-scan jika order belum di-payment
     */
    async _activateCode(code) {
        const rule = this.pos.rules.find((rule) => {
            return rule.mode === "with_code" && (rule.promo_barcode === code || rule.code === code);
        });
        
        let claimableRewards = null;
        let coupon = null;
        
        if (rule) {
            // ========== PROMO CODE RULE (tidak berubah) ==========
            if (
                rule.program_id.date_from &&
                this.date_order < rule.program_id.date_from.startOf("day")
            ) {
                return _t("That promo code program is not yet valid.");
            }
            if (rule.program_id.date_to && this.date_order > rule.program_id.date_to.endOf("day")) {
                return _t("That promo code program is expired.");
            }
            const program_pricelists = rule.program_id.pricelist_ids;
            if (
                program_pricelists.length > 0 &&
                (!this.pricelist || !program_pricelists.includes(this.pricelist.id))
            ) {
                return _t("That promo code program requires a specific pricelist.");
            }
            if (this.codeActivatedProgramRules.includes(rule.id)) {
                return _t("That promo code program has already been activated.");
            }
            this.codeActivatedProgramRules.push(rule.id);
            await this._updateLoyaltyPrograms();
            claimableRewards = this.getClaimableRewards(false, rule.program_id.id);
            
        } else {
            // ========== COUPON CODE (GIFT CARD / VOUCHER) ==========
            
            // 🔑 PERBAIKAN 1: Cek apakah coupon sudah ada di order SAAT INI
            const existingCoupon = this.codeActivatedCoupons.find((c) => c.code === code);
            
            if (existingCoupon) {
                console.log("🔍 Coupon ditemukan di order:", {
                    code: code,
                    coupon_id: existingCoupon.id,
                    order_state: this.state,
                    is_finalized: this.finalized
                });
                
                // 🔑 PERBAIKAN 2: Cek status order
                // Izinkan re-scan hanya jika order belum di-payment/finalized
                const isOrderPending = !this.state || this.state === 'draft' || this.state === 'new';
                const isNotFinalized = !this.finalized;
                
                if (isOrderPending && isNotFinalized) {
                    // ✅ Order masih pending, izinkan re-claim reward
                    console.log("✅ Order masih pending, izinkan re-claim reward dari coupon");
                    
                    // Refresh claimable rewards untuk coupon ini, lalu lanjut ke blok
                    // AUTO-APPLY di bawah (satu tempat untuk apply + pesan ke kasir)
                    coupon = existingCoupon;
                    claimableRewards = this.getClaimableRewards(existingCoupon.id);

                } else {
                    // ❌ Order sudah finalized/paid, tolak
                    console.warn("❌ Order sudah finalized, tolak re-scan coupon");
                    return _t("That coupon code has already been scanned and activated.");
                }
            }

            // 🔑 PERBAIKAN 3: Coupon belum ada di order, fetch dari server
            if (!existingCoupon) {
            console.log("🆕 Coupon baru, fetch dari server:", code);
            
            const customerId = this.get_partner() ? this.get_partner().id : false;
            const { successful, payload } = await this.env.services.orm.call(
                "pos.config",
                "use_coupon_code",
                [
                    [this.pos.config.id],
                    code,
                    this.date_order,
                    customerId,
                    this.pricelist ? this.pricelist.id : false,
                ]
            );
            
            if (successful) {
                // Gift card validation
                const program = this.pos.program_by_id[payload.program_id];
                if (!program) {
                    // Kupon valid di server, tapi program-nya tidak dimuat di POS ini
                    return _t(
                        "Kupon %s valid, tetapi program-nya tidak aktif di POS ini " +
                        "(cek Point of Sale di program, tanggal berlaku, lalu reload POS).",
                        code
                    );
                }
                if (program && program.program_type === "gift_card" && !payload.has_source_order) {
                    const { confirmed } = await this.env.services.popup.add(ConfirmPopup, {
                        title: _t("Unpaid gift card"),
                        body: _t(
                            "This gift card is not linked to any order. Do you really want to apply its reward?"
                        ),
                    });
                    if (!confirmed) {
                        return _t("Unpaid gift card rejected.");
                    }
                }
                
                // 🔑 PERBAIKAN 4: Buat coupon object (tanpa constructor)
                coupon = {
                    code: code,
                    id: payload.coupon_id,
                    program_id: payload.program_id,
                    partner_id: payload.partner_id,
                    balance: payload.points,
                    expiration_date: payload.expiration_date ? new Date(payload.expiration_date) : false,
                    isExpired() {
                        return this.expiration_date && this.expiration_date < new Date();
                    }
                };
                
                console.log("✅ Coupon berhasil dibuat:", coupon);
                
                this.pos.couponCache[coupon.id] = coupon;
                this.codeActivatedCoupons.push(coupon);
                await this._updateLoyaltyPrograms();
                claimableRewards = this.getClaimableRewards(coupon.id);
                
            } else {
                console.error("❌ Gagal validasi coupon:", payload.error_message);
                return payload.error_message;
            }
            } // end if (!existingCoupon)
        }
        
        console.log("🎯 Claimable rewards:", (claimableRewards || []).map((r) => ({
            program: r.reward.program_id?.name,
            reward: r.reward.description,
            type: r.reward.reward_type,
            required_points: r.reward.required_points,
        })), {
            coupon_points: coupon ? this._getRealCouponPoints(coupon.id) : undefined,
            order_total: this.get_total_with_tax(),
            orderlines: this.orderlines.length,
        });

        // ========== GIFT CARD BALANCE INFO ==========
        // Order masih kosong: tampilkan saldo, reward akan otomatis diterapkan
        // oleh _updateRewards saat produk ditambahkan.
        if (!rule && this.orderlines.length === 0 && coupon) {
            return _t(
                "Kupon %s aktif (saldo/poin: %s). Tambahkan produk, reward akan diterapkan otomatis.",
                code,
                coupon.balance
            );
        }

        // ========== AUTO-APPLY REWARD ==========
        if (!claimableRewards || claimableRewards.length === 0) {
            return _t(
                "Kupon %s aktif, tetapi belum ada reward yang memenuhi syarat " +
                "(cek minimal belanja / produk syarat program, atau reward sudah diterapkan).",
                code
            );
        }

        if (claimableRewards.length === 1) {
            const { reward, coupon_id } = claimableRewards[0];
            if (reward.reward_type !== "product" || !reward.multi_product) {
                console.log("🎁 Auto-applying single reward:", reward.description);
                const applyResult = this._applyReward(reward, coupon_id);
                if (applyResult !== true) {
                    // _applyReward mengembalikan string pesan jika gagal
                    return applyResult;
                }
                this._updateRewards();
                return true;
            }
        }

        // Lebih dari 1 reward atau reward multi-produk: kasir harus memilih sendiri
        return _t("Kupon %s aktif. Klik tombol Reward untuk memilih reward.", code);
    },

    /**
     * 🔑 OVERRIDE: activateCode untuk logging yang lebih baik
     */
    async activateCode(code) {
        console.log("🎫 Aktivasi code:", code);
        const res = await this._activateCode(code);
        
        if (res !== true) {
            console.warn("⚠️ Aktivasi gagal:", res);
            this.env.services.pos_notification.add(res, 5000);
        } else {
            console.log("✅ Aktivasi berhasil!");
        }
    },

    /**
     * 🔑 TAMBAHAN: Reset coupon saat order reset
     */
    _resetPrograms() {
        console.log("🔄 Reset programs dipanggil");
        
        // Simpan reference ke parent method jika ada
        const parentReset = super._resetPrograms;
        
        if (parentReset) {
            parentReset.call(this);
        } else {
            // Fallback: reset manual
            this.disabledRewards = new Set();
            this.codeActivatedProgramRules = [];
            this.codeActivatedCoupons = [];
            this.couponPointChanges = {};
            
            const rewardLines = this._get_reward_lines();
            if (rewardLines && rewardLines.length > 0) {
                this.orderlines.remove(rewardLines);
            }
        }
        
        this._updateRewards();
    },
});