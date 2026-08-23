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
		var df_weighed = frappe.meta.get_docfield(
			'Ingredients Weighing Table LGM',
			'weighed',
			frm.doc.name,
		);
		if (df_weighed) {
			df_weighed.hidden = 1;
			df_weighed.read_only = 1;
		}
		frm.refresh_field('weighing_table_lgm');

		frappe.realtime.on('work_order_lgm_status_changed', function (data) {
			if (data.work_order == frm.doc.name) {
				frm.reload_doc();
			}
		});
	},

	refresh: function (frm) {
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
			const cancel_submission = (err_msg) => {
				// 1. Safely unlock the UI
				if (frm.page && frm.page.btn_primary) {
					frm.enable_save();
					frm.page.btn_primary.removeClass('disabled');
					frm.page.btn_primary.prop('disabled', false);
				}

				if (err_msg) {
					frappe.msgprint({
						title: __('Submission Halted'),
						message: err_msg,
						indicator: 'red',
					});
				}

				reject('cancel');
			};

			frm.call({
				method: 'create_material_transfer',
				args: { doc: frm.doc },
				callback: (r) => {
					var response = r.message;

					if (response === true) {
						return resolve();
					}

					if (response === false) {
						return cancel_submission(
							__(
								'A Material Transfer for this Work Order already exists.',
							),
						);
					}

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
							cancel_submission(
								__(
									'Submission cancelled - Please choose different warehouses or initiate a material transfer',
								),
							);
						} else {
							cancel_submission();
						}
					});

					dialog.show();
				},
				error: (r) => {
					let actual_error = null;

					// 1. Check for user-facing errors thrown via Python's frappe.throw()
					if (r && r._server_messages) {
						try {
							let msg_list = JSON.parse(r._server_messages);
							let msg_obj =
								typeof msg_list[0] === 'string'
									? JSON.parse(msg_list[0])
									: msg_list[0];
							actual_error = msg_obj.message || msg_obj;
						} catch (e) {
							// Fallback if JSON parsing fails
						}
					}

					// 2. Fallback to raw exception / execution error if no message was parsed
					if (!actual_error && r && r.exc) {
						actual_error = __(
							'A server error occurred. Check the Error Log for details.',
						);
					}

					// 3. Unlock the form and present the actual backend message
					cancel_submission(
						actual_error ||
							__('An unknown network or server error occurred.'),
					);
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
