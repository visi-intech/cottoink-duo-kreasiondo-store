import logging
import re

from odoo import models, fields, api, _
from odoo.exceptions import AccessError, UserError
from odoo.tools import float_compare

_logger = logging.getLogger(__name__)

TS_IN_GROUP = 'vit_ts_transfer.group_ts_in_creator'
TS_OUT_NAME = 'TS Out'
TS_IN_NAME = 'TS In'


class StockPicking(models.Model):
    _inherit = 'stock.picking'

    vit_ts_transfer_type_id = fields.Many2one('vit.ts.transfer.type', string='Transfer Type',
                                               help='Jenis transaksi untuk TS Out.')
    vit_ts_transfer_out_ref = fields.Char(string='TS Out Reference', index=True, copy=False,
                                           help='Nomor dokumen TS Out yang menjadi dasar TS In ini. '
                                                'Diisi otomatis (dari sync MC atau saat TS In dibuat otomatis).')
    vit_ts_source_warehouse_id = fields.Many2one('stock.warehouse', string='Source Warehouse',
                                                  index=True, copy=False,
                                                  help='Gudang asal. Hanya dipakai di form TS Out '
                                                       '(di TS In yang tampil adalah Source Warehouse teks).')
    vit_ts_source_warehouse = fields.Char(string='Source Warehouse', index=True, copy=False,
                                          help='Nama gudang asal (teks biasa, bukan relasi ke data gudang). '
                                               'Diisi otomatis (dari sync MC atau saat TS In dibuat otomatis).')
    vit_ts_warehouse_dest_id = fields.Many2one('stock.warehouse', string='Destination Warehouse',
                                                index=True, copy=False,
                                                help='Gudang tujuan akhir. Pada TS Out dipakai untuk menentukan '
                                                     'lokasi tujuan TS In (stock gudang ini).')
    vit_ts_is_ts_in = fields.Boolean(string='Is TS In', compute='_compute_vit_ts_is_ts_in',
                                      help='Technical: dokumen ini memakai operation type TS In.')
    vit_ts_is_ts_out = fields.Boolean(string='Is TS Out', compute='_compute_vit_ts_is_ts_out',
                                       help='Technical: dokumen ini memakai operation type TS Out.')
    vit_ts_in_count = fields.Integer(string='TS In', compute='_compute_vit_ts_in_count')

    @staticmethod
    def _vit_ts_normalize_type_name(name):
        """Samakan nama operation type: abaikan huruf besar/kecil dan spasi
        berlebih/tersembunyi (termasuk non-breaking space)."""
        return ' '.join((name or '').split()).casefold()

    def _vit_ts_type_matches(self, picking_type, target):
        """True kalau nama operation type == target (toleran: huruf besar/kecil, spasi,
        dan dicek di bahasa user + en_US karena name translatable)."""
        wanted = self._vit_ts_normalize_type_name(target)
        names = {
            self._vit_ts_normalize_type_name(picking_type.name),
            self._vit_ts_normalize_type_name(picking_type.with_context(lang='en_US').name),
        }
        return wanted in names

    @api.depends('picking_type_id.name')
    def _compute_vit_ts_is_ts_in(self):
        for picking in self:
            picking.vit_ts_is_ts_in = self._vit_ts_type_matches(picking.picking_type_id, TS_IN_NAME)

    @api.depends('picking_type_id.name')
    def _compute_vit_ts_is_ts_out(self):
        for picking in self:
            picking.vit_ts_is_ts_out = self._vit_ts_type_matches(picking.picking_type_id, TS_OUT_NAME)

    # ------------------------------------------------------------------
    # Penomoran: TSOT/<kode WH>/<tahun>/00001 dan TSIN/<kode WH>/<tahun>/00001.
    # Di Store kode WH diambil dari Destination Warehouse. Dokumen hasil sync dari MC membawa
    # nomor MC (name sudah terisi) dan tidak diberi nomor baru di sini, supaya nomor Store = MC.
    # ------------------------------------------------------------------
    @api.model
    def _vit_ts_numbering_warehouse(self, vals, picking_type):
        warehouse = self.env['stock.warehouse']
        if vals.get('vit_ts_warehouse_dest_id'):
            warehouse = warehouse.browse(vals['vit_ts_warehouse_dest_id'])
        if not warehouse and vals.get('location_dest_id'):
            warehouse = self.env['stock.location'].browse(vals['location_dest_id']).warehouse_id
        return warehouse or picking_type.warehouse_id

    @api.model
    def _vit_ts_next_name(self, prefix, code, batch_counter):
        """Nomor berikutnya = nomor terbesar yang sudah dipakai prefix yang sama + 1 (termasuk
        dokumen hasil sync dari MC). Advisory lock menyerialkan pembuatan nomor."""
        year = fields.Date.context_today(self).year
        base = '%s/%s/%s/' % (prefix, code, year)
        if base not in batch_counter:
            self.env.cr.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (base,))
            like = base.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
            pattern = '^%s[0-9]+$' % re.escape(base)
            self.env.cr.execute(
                "SELECT max(substring(name from '([0-9]+)$')::bigint) FROM stock_picking "
                "WHERE name LIKE %s AND name ~ %s", (like, pattern))
            batch_counter[base] = self.env.cr.fetchone()[0] or 0
        batch_counter[base] += 1
        return '%s%05d' % (base, batch_counter[base])

    # ------------------------------------------------------------------
    # Destination Warehouse TS Out: dipilih lewat target_location (master.warehouse, hasil sync),
    # lalu dicocokkan ke stock.warehouse lewat kode gudang, fallback nama gudang.
    # ------------------------------------------------------------------
    @api.model
    def _vit_ts_match_stock_warehouse(self, master_warehouse):
        """stock.warehouse yang cocok dengan master.warehouse (kode dulu, lalu nama).
        Hanya dikembalikan kalau hasilnya tepat satu; kalau nol atau ambigu -> recordset kosong."""
        Warehouse = self.env['stock.warehouse']
        master = master_warehouse.sudo()
        if not master:
            return Warehouse
        # gudang jumlahnya kecil; dibandingkan di Python supaya huruf besar/kecil dan spasi
        # berlebih tidak jadi masalah (tanpa pola LIKE yang perlu di-escape)
        warehouses = Warehouse.search([])
        norm = lambda v: ' '.join((v or '').split()).casefold()
        for field, master_value in (('code', master.warehouse_code), ('name', master.warehouse_name)):
            wanted = norm(master_value)
            if not wanted:
                continue
            found = warehouses.filtered(lambda w: norm(w[field]) == wanted)
            if len(found) == 1:
                return found
        return Warehouse

    @api.model_create_multi
    def create(self, vals_list):
        batch_counter = {}
        for vals in vals_list:
            type_id = vals.get('picking_type_id') or self.default_get(['picking_type_id']).get('picking_type_id')
            if not type_id:
                continue
            picking_type = self.env['stock.picking.type'].browse(type_id)
            # TS Out: isi Destination Warehouse dari target_location (sebelum penomoran memakainya)
            if (vals.get('target_location') and not vals.get('vit_ts_warehouse_dest_id')
                    and self._vit_ts_type_matches(picking_type, TS_OUT_NAME)):
                warehouse = self._vit_ts_match_stock_warehouse(
                    self.env['master.warehouse'].browse(vals['target_location']))
                if warehouse:
                    vals['vit_ts_warehouse_dest_id'] = warehouse.id
                else:
                    _logger.warning("TS Out: target_location %s tidak cocok dengan stock.warehouse manapun",
                                    vals['target_location'])
            if vals.get('name') not in (None, False, '', '/'):
                continue
            if self._vit_ts_type_matches(picking_type, TS_OUT_NAME):
                prefix = 'TSOT'
            elif self._vit_ts_type_matches(picking_type, TS_IN_NAME):
                prefix = 'TSIN'
            else:
                continue
            code = (self._vit_ts_numbering_warehouse(vals, picking_type).code or '').strip()
            if code:  # tanpa kode WH: biarkan penomoran bawaan Odoo
                vals['name'] = self._vit_ts_next_name(prefix, code, batch_counter)
        return super().create(vals_list)

    @api.model
    def default_get(self, fields_list):
        """Create New dari menu TS Out / TS In: isi Operation Type otomatis, supaya
        vit_ts_is_ts_out / vit_ts_is_ts_in sudah benar sejak form baru dan layout TS Out/TS In
        langsung tampil. Penandanya context `vit_ts_default_kind` ('ts_out' / 'ts_in');
        tanpa penanda itu (menu lain) perilaku bawaan Odoo tidak berubah."""
        res = super().default_get(fields_list)
        kind = self.env.context.get('vit_ts_default_kind')
        if kind in ('ts_out', 'ts_in') and 'picking_type_id' in fields_list and not res.get('picking_type_id'):
            target = TS_OUT_NAME if kind == 'ts_out' else TS_IN_NAME
            types = self.env['stock.picking.type'].search(
                [('company_id', '=', self.env.company.id)]).filtered(
                lambda t: self._vit_ts_type_matches(t, target))
            if types:
                res['picking_type_id'] = types[0].id
        return res

    def write(self, vals):
        res = super().write(vals)
        # TS Out: target_location berubah -> Destination Warehouse ikut disesuaikan
        if 'target_location' in vals and 'vit_ts_warehouse_dest_id' not in vals:
            for picking in self.filtered('vit_ts_is_ts_out'):
                warehouse = self._vit_ts_match_stock_warehouse(picking.target_location)
                if warehouse and picking.vit_ts_warehouse_dest_id != warehouse:
                    picking.vit_ts_warehouse_dest_id = warehouse
        return res

    @api.onchange('target_location')
    def _onchange_vit_ts_target_location(self):
        """TS Out: Destination Warehouse (stock.warehouse) diisi dari target_location, lalu
        Destination Location diisi transit location gudang itu (kalau ada), kalau tidak stock
        location gudang itu."""
        if not self.vit_ts_is_ts_out:
            return
        warehouse = self._vit_ts_match_stock_warehouse(self.target_location)
        self.vit_ts_warehouse_dest_id = warehouse
        if warehouse:
            self.location_dest_id = warehouse.location_transit or warehouse.lot_stock_id
        elif self.target_location:
            return {'warning': {
                'title': _("Gudang tidak cocok"),
                'message': _("Destination Warehouse '%s' tidak cocok dengan gudang manapun "
                             "(kode atau nama). Destination Location dan TS In otomatis tidak bisa "
                             "ditentukan; cek kode gudang di master data.",
                             self.target_location.display_name)}}

    @api.onchange('vit_ts_warehouse_dest_id')
    def _onchange_vit_ts_warehouse_dest_id(self):
        """Destination Location TS Out diisi transit location gudang tujuan (kalau ada),
        kalau tidak stock location gudang itu."""
        if self.vit_ts_warehouse_dest_id and self.vit_ts_is_ts_out:
            warehouse = self.vit_ts_warehouse_dest_id
            self.location_dest_id = warehouse.location_transit or warehouse.lot_stock_id

    def _vit_ts_get_ts_in(self):
        """TS In (non-cancel) yang merujuk ke nomor TS Out ini lewat field char."""
        self.ensure_one()
        if not self.name:
            return self.browse()
        return self.search([('vit_ts_transfer_out_ref', '=', self.name), ('state', '!=', 'cancel')])

    def _compute_vit_ts_in_count(self):
        for picking in self:
            picking.vit_ts_in_count = len(picking._vit_ts_get_ts_in()) if picking.vit_ts_is_ts_out else 0

    # ------------------------------------------------------------------
    # TS Out -> TS In
    # ------------------------------------------------------------------
    def _vit_ts_check_ts_in_access(self):
        if not self.env.user.has_group(TS_IN_GROUP):
            raise AccessError(_("Kamu tidak punya hak akses untuk membuat / melihat dokumen TS In."))

    def _vit_ts_get_ts_in_picking_type(self):
        """Operation type TS In; utamakan milik warehouse tujuan TS Out."""
        self.ensure_one()
        # filter di Python (bukan ilike) supaya nama dengan spasi tersembunyi tetap ketemu;
        # jumlah operation type kecil, jadi aman.
        types = self.env['stock.picking.type'].search([]).filtered(
            lambda t: self._vit_ts_type_matches(t, TS_IN_NAME))
        if not types:
            raise UserError(_("Operation Type '%s' tidak ditemukan.", TS_IN_NAME))
        dest_warehouse = self.vit_ts_warehouse_dest_id or self.location_dest_id.warehouse_id
        preferred = types.filtered(lambda t: t.warehouse_id == dest_warehouse) if dest_warehouse else types.browse()
        return (preferred or types)[:1]

    def _vit_ts_prepare_ts_in_values(self, picking_type, move_vals):
        self.ensure_one()
        # Tujuan TS In: stock location Destination Warehouse yang diisi di TS Out.
        # Fallback (TS Out lama yang belum punya gudang tujuan): setting operation type TS In.
        if self.vit_ts_warehouse_dest_id:
            dest_location = self.vit_ts_warehouse_dest_id.lot_stock_id
            if not dest_location:
                raise UserError(_("Gudang %s belum punya Location Stock.", self.vit_ts_warehouse_dest_id.display_name))
        else:
            dest_location = picking_type.default_location_dest_id or picking_type.warehouse_id.lot_stock_id
        if not dest_location:
            raise UserError(_("Isi Destination Warehouse di TS Out, atau set Default Destination "
                              "Location pada Operation Type %s.", picking_type.display_name))
        if dest_location == self.location_dest_id:
            raise UserError(_("Lokasi tujuan TS In (%s) sama dengan lokasi asalnya (Destination Location "
                              "TS Out). Perpindahan antar gudang harus lewat lokasi transit: isi Transit "
                              "Location pada gudang tujuan, lalu pakai sebagai Destination Location TS Out.",
                              dest_location.display_name))
        for _cmd, _id, vals in move_vals:
            vals['location_dest_id'] = dest_location.id
        return {
            'picking_type_id': picking_type.id,
            'location_id': self.location_dest_id.id,
            'location_dest_id': dest_location.id,
            'origin': self.name,
            'company_id': self.company_id.id,
            'vit_ts_transfer_type_id': self.vit_ts_transfer_type_id.id,
            'vit_ts_transfer_out_ref': self.name,
            'vit_ts_source_warehouse': (self.vit_ts_source_warehouse_id.name or self.vit_ts_source_warehouse
                                        or self.location_id.warehouse_id.name),
            'vit_ts_warehouse_dest_id': self.vit_ts_warehouse_dest_id.id,
            'move_ids_without_package': move_vals,
        }

    def _vit_ts_create_ts_in(self):
        """Buat TS In dari TS Out yang sudah Done, sesuai qty yang BENAR-BENAR dikirim
        (move.quantity). Kalau TS Out divalidasi sebagian, backorder-nya adalah picking
        terpisah dan akan membuat TS In sendiri saat divalidasi."""
        self.ensure_one()
        if not self.vit_ts_is_ts_out or self.state != 'done':
            return self.browse()
        # idempotent: satu TS Out cuma dibuatkan satu TS In
        if self._vit_ts_get_ts_in():
            return self.browse()

        move_vals = []
        for move in self.move_ids.filtered(lambda m: m.state == 'done'):
            if float_compare(move.quantity, 0.0, precision_rounding=move.product_uom.rounding) <= 0:
                continue
            move_vals.append((0, 0, {
                'name': move.product_id.display_name,
                'product_id': move.product_id.id,
                'product_uom': move.product_uom.id,
                'product_uom_qty': move.quantity,
                'location_id': self.location_dest_id.id,
                'location_dest_id': self.location_dest_id.id,  # ditimpa di _vit_ts_prepare_ts_in_values
                'vit_ts_out_move_id': move.id,
            }))
        if not move_vals:
            return self.browse()

        picking_type = self._vit_ts_get_ts_in_picking_type()
        return self.env['stock.picking'].create(self._vit_ts_prepare_ts_in_values(picking_type, move_vals))

    def _action_done(self):
        """Hook di picking (bukan button_validate) supaya TS In tetap terbentuk lewat
        wizard backorder / immediate transfer maupun validasi dari kode."""
        res = super()._action_done()
        for picking in self.filtered(lambda p: p.state == 'done' and p.vit_ts_is_ts_out):
            try:
                # savepoint: kegagalan bikin TS In tidak boleh membatalkan validasi TS Out
                with self.env.cr.savepoint():
                    ts_in = picking._vit_ts_create_ts_in()
            except Exception as exc:  # noqa: BLE001 - dicatat di log + chatter, bukan ditelan
                _logger.exception("Gagal membuat TS In otomatis dari %s", picking.name)
                picking.message_post(body=_(
                    "TS In otomatis GAGAL dibuat: %s. Cek Operation Type TS In "
                    "(Default Destination Location) lalu buat TS In secara manual.", str(exc)))
                continue
            if ts_in:
                picking.message_post(body=_("TS In %s dibuat otomatis setelah TS Out ini Done.", ts_in.name))
                ts_in._vit_ts_auto_ready()
        return res

    def _vit_ts_auto_ready(self):
        """TS In yang baru terbentuk langsung dikonfirmasi + di-reserve supaya statusnya
        Ready (bukan Draft). Dipisah dari pembuatan TS In: kalau gagal, TS In tetap ada
        (Draft) dan error-nya dicatat di chatter, tidak membatalkan TS Out."""
        self.ensure_one()
        try:
            with self.env.cr.savepoint():
                if self.state == 'draft':
                    self.action_confirm()
                if self.state == 'confirmed':
                    self.action_assign()
        except Exception as exc:  # noqa: BLE001 - dicatat di log + chatter
            _logger.exception("Gagal membuat TS In %s jadi Ready", self.name)
            self.message_post(body=_(
                "TS In gagal otomatis jadi Ready: %s. Klik Confirm / Check Availability manual.", str(exc)))
            return
        if self.state != 'assigned':
            self.message_post(body=_(
                "TS In sudah dikonfirmasi tapi belum Ready (stok di lokasi asal belum tersedia "
                "penuh). Status saat ini: %s.", dict(self._fields['state'].selection).get(self.state)))

    def vit_ts_action_view_ts_in(self):
        self.ensure_one()
        self._vit_ts_check_ts_in_access()
        return {
            'type': 'ir.actions.act_window',
            'name': _('TS In'),
            'res_model': 'stock.picking',
            'view_mode': 'tree,form',
            'domain': [('vit_ts_transfer_out_ref', '=', self.name)],
            'context': {'create': False},
        }


class StockMove(models.Model):
    _inherit = 'stock.move'

    vit_ts_out_move_id = fields.Many2one('stock.move', string='TS Out Move',
                                          readonly=True, index=True, copy=False,
                                          help='Move pada TS Out yang menjadi dasar move TS In ini.')
