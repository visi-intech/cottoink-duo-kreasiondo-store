# -*- coding: utf-8 -*-
"""Integrasi PoS dengan modul stock_no_negative.

Setting-nya HANYA dibaca dari lokasi sumber PoS
(pos.config -> picking_type_id -> default_location_src_id), field
"Allow Negative Stock" di Inventory > Configuration > Locations:
  - TIDAK dicentang : produk storable yang stoknya kosong tidak bisa dipilih
                      maupun di-scan di PoS (vit_pos_stock_control = True).
  - Dicentang       : PoS bebas menjual walau stok kosong.
Setting allow_negative_stock di produk / kategori tidak dipakai di PoS.
Produk non-storable (consumable / service) tidak punya stok, jadi tidak dicek.

vit_pos_qty = stok di lokasi sumber PoS dikurangi qty order PoS yang sudah
terbayar tapi belum punya picking (mode update stok "saat sesi ditutup"),
supaya stok di PoS tidak terlihat masih ada padahal sudah terjual.
"""
from odoo import api, models


class ProductProduct(models.Model):
    _inherit = "product.product"

    @api.model
    def vit_get_pos_stock(self, config_id, product_ids):
        """Dipanggil dari PoS: {product_id: {'qty': float, 'control': bool}}."""
        config = self.env["pos.config"].browse(config_id).exists()
        products = self.browse(product_ids).exists()
        return products._vit_pos_stock_data(config)

    def _vit_pos_stock_data(self, config):
        res = {p.id: {"qty": 0.0, "control": False} for p in self}
        location = config.picking_type_id.default_location_src_id if config else False
        if not self or not location or location.allow_negative_stock:
            return res

        # Kontrol stok hanya ditentukan oleh setting lokasi (di atas);
        # cukup produk storable yang punya stok untuk dicek.
        controlled = self.sudo().filtered(lambda p: p.type == "product")
        if not controlled:
            return res

        # Qty yang sudah terjual di PoS tapi stoknya belum dipotong (belum ada picking).
        pending = {}
        groups = self.env["pos.order.line"].sudo().read_group(
            [
                ("product_id", "in", controlled.ids),
                ("order_id.state", "in", ["paid", "done", "invoiced"]),
                ("order_id.picking_ids", "=", False),
                ("order_id.session_id.state", "!=", "closed"),
                ("order_id.config_id.picking_type_id.default_location_src_id", "=", location.id),
            ],
            ["qty:sum"],
            ["product_id"],
        )
        for g in groups:
            pending[g["product_id"][0]] = g["qty"] or 0.0

        for product in controlled.with_context(location=location.id):
            res[product.id] = {
                "qty": product.qty_available - pending.get(product.id, 0.0),
                "control": True,
            }
        return res


class PosSession(models.Model):
    _inherit = "pos.session"

    def _process_pos_ui_product_product(self, products):
        # Dipakai saat load awal, background loading, dan _loadMissingProducts,
        # jadi semua produk di PoS selalu membawa info stok terbaru.
        res = super()._process_pos_ui_product_product(products)
        ids = [p["id"] for p in products]
        stock = self.env["product.product"].browse(ids)._vit_pos_stock_data(self.config_id)
        for p in products:
            vals = stock.get(p["id"], {})
            p["vit_pos_qty"] = vals.get("qty", 0.0)
            p["vit_pos_stock_control"] = vals.get("control", False)
        return res
