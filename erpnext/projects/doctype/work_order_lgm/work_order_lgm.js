// Copyright (c) 2023, Frappe Technologies Pvt. Ltd. and contributors
// For license information, please see license.txt

frappe.ui.form.on('Work Order LGM', {
	setup: function (frm) {
		frm.call({
			method: 'get_all_work_order',
			callback: function (r) {
				var sheet_name = r.message;
				// prevent duplicate work order of the same request sheet
				frm.set_query('request_sheet_link', function () {
					return {
						filters: {
							name: ['not in', sheet_name],
						},
					};
				});
			},
		});
	},

	onload: function (frm) {
		frappe.realtime.on('work_order_lgm_status_changed', function (data) {
			if (data.work_order == frm.doc.name) {
				frm.reload_doc();
			}
		});
	},

	refresh: function (frm) {
		if (!frm.__reloaded_this_view) {
			frm.__reloaded_this_view = true;
			frm.reload_doc();
			return;
		}
		frm.__reloaded_this_view = false;

		if (frm.doc.status) {
			var status_colors = {
				'Not Started': 'orange',
				'In Progress': 'blue',
				'Completed': 'green',
			};
			frm.page.set_indicator(
				__(frm.doc.status),
				status_colors[frm.doc.status] || 'grey',
			);
		}

		if (frm.doc.docstatus === 1) {
			frm.add_custom_button(__('Create Job Card LGM'), function () {
				frm.call({
					method: 'create_job_card_lgm',
					args: {
						doc: frm.doc,
					},
					callback: function (r) {
						frappe.msgprint({
							message: __('Job Card is created'),
							indicator: 'green',
						});
						frm.reload_doc();
						return;
					},
				});
			});
		}
	},

	before_submit(frm) {
		var ingredients_list = frm.doc['weighing_table_lgm'] || [];
		var no_of_ingredients = frm.doc['weighing_table_lgm'].length;
		for (var i = 0; i < no_of_ingredients; i++) {
			if (ingredients_list[i]['weighed'] == undefined) {
				frm.reload_doc();
				frappe.throw({
					message: __(`Ingredient ${i + 1} weight is not measured yet.`),
					indicator: 'red',
				});
			}
			if (!ingredients_list[i]['source_warehouse']) {
				frappe.throw({
					message: __(
						`Ingredient ${i + 1} (${ingredients_list[i]['ingredient']}) has no Source Warehouse set.`,
					),
					indicator: 'red',
				});
			}
		}

		// The outer Promise safely pauses the save/submit event
		return new Promise((resolve, reject) => {
			const cancel_submission = () => {
				if (frm.page && frm.page.btn_primary) {
					frm.enable_save();
					frm.page.btn_primary.removeClass('disabled');
					frm.page.btn_primary.prop('disabled', false);
				}
				reject('cancel');
			};

			frm.call({
				method: 'create_material_transfer',
				args: { doc: frm.doc },
				callback: (r) => {
					var response = r.message;

					// Success path
					if (response === true) {
						return resolve();
					}

					// Handle Shortfalls
					var shortfall_html = response
						.map(
							(s) =>
								`<li><b>${s.ingredient}:</b> needs ${s.needed}, only ${s.available} in ${s.source_warehouse}</li>`,
						)
						.join('');

					var is_routing = false;

					var dialog = new frappe.ui.Dialog({
						title: __('Insufficient Stock'),
						fields: [
							{
								fieldname: 'shortfall_info',
								fieldtype: 'HTML',
								options: `<p>The following ingredients lack sufficient stock:</p>
									<ul>${shortfall_html}</ul>
									<p>You can manually change the source warehouse, 
									or initiate a material tranfer</p>`,
							},
						],
						primary_action_label: __('Manual Material Transfer'),
						primary_action: () => {
							is_routing = true;
							dialog.hide();

							frappe.route_options = {
								stock_entry_type: 'Material Transfer',
								work_order_lgm: frm.doc.name,
							};

							frappe.new_doc('Stock Entry');
							cancel_submission();
						},
					});

					dialog.$wrapper.on('hidden.bs.modal', function () {
						if (!is_routing) {
							frappe.msgprint({
								title: __('Cancelled'),
								message: __(
									'Submission cancelled - Please choose different warehouses or initiate a material transfer',
								),
								indicator: 'red',
							});
						}
						cancel_submission();
					});

					dialog.show();
				},
				error: (r) => {
					// Safely unlock the UI if the backend throws a 500 or times out
					cancel_submission();
				},
			});
		});
	},
});

// child table
frappe.ui.form.on('Ingredients Weighing Table LGM', {
	// cdt is Child DocType name i.e Quotation Item
	// cdn is the row name for e.g bbfcb8da6a
});
