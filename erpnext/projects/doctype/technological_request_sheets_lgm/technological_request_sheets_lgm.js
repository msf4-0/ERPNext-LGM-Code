// Copyright (c) 2023, Frappe Technologies Pvt. Ltd. and contributors
// For license information, please see license.txt

frappe.ui.form.on('Technological Request Sheets LGM', {
	setup: function (frm) {
		frm.custom_make_buttons = {
			'Work Order': 'Create Work Order',
		};
	},

	refresh: function (frm) {
		console.log(frm.doc.docstatus);
		if (frm.doc.docstatus === 1) {
			frm.add_custom_button(__('Create Work Order LGM'), function () {
				frm.call({
					method: 'create_work_order_lgm',
					args: {
						doc: frm.doc,
					},
					callback: function (r) {
						if (r.message) {
							var work_order_name = r.message;

							frappe.msgprint({
								message: __('Work Order LGM is created'),
								indicator: 'green',
								primary_action: {
									label: __('View Work Order'),
									action: function () {
										frappe.set_route(
											'Form',
											'Work Order LGM',
											work_order_name,
										);
										frappe.hide_msgprint();
									},
								},
							});

							frm.reload_doc();
						}
					},
				});
			});
		}
	},

	before_save: function (frm) {
		frm.call({
			method: 'calculate_waste',
			args: {
				doc: frm.doc,
			},
			callback: function (r) {
				frm.set_value('mb_waste', r.message);
			},
		});
	},
});
