import re
import requests
from datetime import datetime, timedelta
import pytz
from odoo import models, fields, api, SUPERUSER_ID
from odoo.exceptions import UserError
import random

TRANSFER_TYPE_KEYS = {
    'out': {'TSOUT', 'TRANSFEROUT', 'TRANSFERSTOCKOUT'},
    'in': {'TSIN', 'TRANSFERIN', 'TRANSFERSTOCKIN'},
}


class StockPickingType(models.Model):
    _inherit = 'stock.picking.type'

    id_mc = fields.Char(string="ID MC", default=False)
    vit_transfer_type = fields.Selection(
        [('out', 'Transfer Out'), ('in', 'Transfer In')],
        string='Jenis Transfer Antar Gudang',
        compute='_compute_vit_transfer_type', store=True, readonly=False, index=True,
        help="Dipakai integrasi Store <-> HO untuk mengenali dokumen Transfer Out / "
             "Transfer In, apa pun nama operation type-nya (TS Out, TSOUT, "
             "Transfer Out, dst). Terisi otomatis dari nama, bisa diubah manual.")

    @api.depends('name')
    def _compute_vit_transfer_type(self):
        for rec in self:
            key = re.sub(r'[^A-Z]', '', (rec.name or '').upper())
            if key in TRANSFER_TYPE_KEYS['out']:
                rec.vit_transfer_type = 'out'
            elif key in TRANSFER_TYPE_KEYS['in']:
                rec.vit_transfer_type = 'in'
            else:
                rec.vit_transfer_type = rec.vit_transfer_type or False