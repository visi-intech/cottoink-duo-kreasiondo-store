# -*- coding: utf-8 -*-
from odoo import fields, models


class VitMerchandiseCategory(models.Model):
    _name = 'vit.merchandise.category'
    _description = 'Merchandise Category'
    # WAJIB: sync MC->SS memetakan Many2one produk lewat Artikel Code
    _rec_name = 'vit_artikel_code'
    _order = 'vit_artikel_code'

    vit_artikel_code = fields.Char(string="Artikel Code", size=50, required=True, index=True, readonly=True)
    vit_size_range = fields.Char(string="Size Range", size=50, readonly=True)
    vit_mc_code = fields.Char(string="MC Code", size=20, readonly=True)
    vit_flag_code = fields.Char(string="Flag Code", size=5, readonly=True)
    vit_flag = fields.Char(string="Flag Name", size=100, readonly=True)
    vit_style_code = fields.Char(string="Style Code", size=5, readonly=True)
    vit_style = fields.Char(string="Style Name", size=100, readonly=True)
    vit_sub_category_code = fields.Char(string="Sub Category Code", size=5, readonly=True)
    vit_sub_category = fields.Char(string="Sub Category Name", size=100, readonly=True)
    vit_category_code = fields.Char(string="Category Code", size=5, readonly=True)
    vit_category = fields.Char(string="Category Name", size=100, readonly=True)
    vit_sub_brand_code = fields.Char(string="Sub Brand Code", size=5, readonly=True)
    vit_sub_brand = fields.Char(string="Sub Brand Name", size=100, readonly=True)
    vit_brand_code = fields.Char(string="Brand Code", size=5, readonly=True)
    vit_brand = fields.Char(string="Brand Name", size=100, readonly=True)
    vit_color_code = fields.Char(string="Color Code", size=5, readonly=True)
    vit_color = fields.Char(string="Color Name", size=100, readonly=True)
    vit_size_code = fields.Char(string="Size Code", size=5, readonly=True)
    vit_size = fields.Char(string="Size Name", size=100, readonly=True)
    vit_label_code = fields.Char(string="Label Code", size=5, readonly=True)
    vit_label = fields.Char(string="Label Name", size=100, readonly=True)
    vit_week_code = fields.Char(string="Week Code", size=5, readonly=True)
    vit_week = fields.Char(string="Week Name", size=100, readonly=True)
    vit_quarter_code = fields.Char(string="Quarter Code", size=5, readonly=True)
    vit_quarter = fields.Char(string="Quarter Name", size=100, readonly=True)
    vit_collection = fields.Char(string="Collection Name", size=200, readonly=True)
    vit_upload_by = fields.Char(string="Upload By", size=200, readonly=True)
    vit_excel_filename = fields.Char(string="Excel FileName", size=254, readonly=True)

    active = fields.Boolean(default=True)
    id_mc = fields.Char(string="ID MC", readonly=True)  # diisi oleh sync dari MC

    _sql_constraints = [
        ('vit_artikel_code_unique', 'unique(vit_artikel_code)', 'Artikel Code sudah ada!'),
    ]
