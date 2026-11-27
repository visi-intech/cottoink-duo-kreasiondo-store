# -*- coding: utf-8 -*-
"""Master data Transfer Type ditambahkan lewat data/transfer_type_data.xml.

Di database yang sudah punya Transfer Type dengan nama yang sama (dibuat manual atau dari
sync MC), Odoo akan membuat record baru karena xml id-nya belum terdaftar, lalu gagal di
unique(name):
    duplicate key value violates unique constraint "vit_ts_transfer_type_name_uniq"

Script ini jalan SEBELUM file data di-load: record yang namanya sudah ada didaftarkan ke
xml id modul, jadi Odoo memakai record yang ada (tidak membuat dobel). Nama yang belum ada
tetap dibuat oleh file XML.
"""
import logging

_logger = logging.getLogger(__name__)

MODULE = 'vit_ts_transfer'
MODEL = 'vit.ts.transfer.type'
TABLE = 'vit_ts_transfer_type'
TYPES = [
    ('transfer_type_alokasi', 'Alokasi'),
    ('transfer_type_peminjaman_store', 'Peminjaman Store'),
    ('transfer_type_peminjaman_wh', 'Peminjaman WH'),
    ('transfer_type_konsolidasi_cdk', 'Konsolidasi CDK'),
    ('transfer_type_konsolidasi_consigment', 'Konsolidasi Consigment'),
    ('transfer_type_retur_reject', 'Retur/Reject'),
    ('transfer_type_retur_slow_moving', 'Retur Slow moving'),
]


def migrate(cr, version):
    cr.execute("SELECT to_regclass(%s)", (TABLE,))
    if not cr.fetchone()[0]:
        return
    adopted = 0
    for xml_id, name in TYPES:
        cr.execute("SELECT id FROM ir_model_data WHERE module = %s AND name = %s", (MODULE, xml_id))
        if cr.fetchone():
            continue
        cr.execute("SELECT id FROM " + TABLE + " WHERE name = %s ORDER BY id LIMIT 1", (name,))
        row = cr.fetchone()
        if not row:
            continue
        cr.execute(
            "INSERT INTO ir_model_data (module, name, model, res_id, noupdate) "
            "VALUES (%s, %s, %s, %s, TRUE)",
            (MODULE, xml_id, MODEL, row[0]))
        adopted += 1
    _logger.info("%s: %s Transfer Type yang sudah ada didaftarkan ke xml id modul", MODULE, adopted)
