{
    'name': 'VIT TS Transfer (TS Out / TS In)',
    'version': '17.0.1.0.0',
    'category': 'Inventory',
    'summary': 'Transfer antar gudang TS Out -> TS In otomatis',
    'description': """
Transfer antar gudang dengan dua operation type:
    TS Out  barang keluar dari gudang asal menuju lokasi transit gudang tujuan
    TS In   dibuat otomatis saat TS Out Done, barang masuk ke stock gudang tujuan

Salinan mandiri dari fitur TS Out / TS In pada modul vit_transfer_request,
tanpa Transfer Request / MD. Berdiri sendiri (depends cuma stock).
    """,
    'author': 'Visi Intech',
    'company': 'Visi Intech',
    'maintainer': 'Visi Intech',
    'website': 'https://www.Visi-Intech.com',
    'depends': ['stock'],
    'data': [
        'security/security_groups.xml',
        'security/ir.model.access.csv',
        'views/stock_warehouse_view.xml',
        'views/transfer_menu.xml',
        'views/stock_picking_view.xml',
        'views/report_deliveryslip.xml',
    ],
    'license': 'LGPL-3',
    'installable': True,
    'auto_install': False,
    'application': False,
}
