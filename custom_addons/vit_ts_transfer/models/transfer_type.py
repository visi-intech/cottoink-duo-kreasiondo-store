from odoo import models, fields


class TransferType(models.Model):
    """Master data jenis transaksi untuk TS Out.

    Isinya sengaja cuma jenis transaksinya saja, tanpa keterangan/skenario.
    """
    _name = 'vit.ts.transfer.type'
    _description = 'TS Transfer Type'
    _order = 'name'

    name = fields.Char(string='Jenis Transaksi', required=True)
    active = fields.Boolean(string='Active', default=True)

    _sql_constraints = [
        ('name_uniq', 'unique(name)', 'Jenis Transaksi ini sudah ada.'),
    ]
