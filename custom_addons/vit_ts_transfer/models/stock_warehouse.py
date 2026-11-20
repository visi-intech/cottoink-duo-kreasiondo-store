from odoo import models, fields


class StockWarehouse(models.Model):
    _inherit = 'stock.warehouse'

    # Nama + tipe sengaja identik dengan field di dev_pos (store_warehouse.py), jadi kalau
    # dua modul terpasang bareng, field/kolom DB-nya sama dan tidak bentrok.
    location_transit = fields.Many2one(
        'stock.location', string="Transit Location",
        help="Lokasi transit gudang ini. Diisi manual. Dipakai sebagai Destination Location "
             "TS Out kalau gudang ini jadi gudang tujuan; kalau kosong dipakai stock location gudang.")
