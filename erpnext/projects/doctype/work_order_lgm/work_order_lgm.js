// Copyright (c) 2023, Frappe Technologies Pvt. Ltd. and contributors
// For license information, please see license.txt

frappe.ui.form.on('Work Order LGM', {
	setup: function(frm){
		frm.call({
			method: "get_all_work_order",
			callback:function(r){
				var sheet_name = r.message;
				// prevent duplicate work order of the same request sheet
				frm.set_query("request_sheet_link", function(){
					return {
						filters: {
							"name": ["not in", sheet_name]
						}
					};
				})
			}
		});
	},

	before_save: function(frm){
		if (frm.doc["weighing_table_lgm"] == undefined){
			// returning the frm.call promise here is required: before_save must wait
			// for the server round-trip to finish before the save proceeds, otherwise
			// the Work Order could save with an empty weighing_table_lgm if the
			// response arrives after Frappe has already read frm.doc for saving.
			return frm.call({
				method: "create_work_order_lgm",
				args:{
					doc:frm.doc
				}
			}).then((r) => {
				// clear first, then add each row through frm.add_child so Frappe
				// attaches the child-table bookkeeping (name, parent, parentfield,
				// idx) that a raw "frm.doc = r.message" assignment would be missing,
				// and so the grid actually re-renders
				frm.doc.weighing_table_lgm = [];
				(r.message.weighing_table_lgm || []).forEach((row) => {
					frm.add_child("weighing_table_lgm", {
						'ingredient_type': row.ingredient_type,
						'mixer_no': row.mixer_no,
						'ingredient': row.ingredient,
						'ingredient_weight': row.ingredient_weight,
						'source_warehouse': row.source_warehouse
					});
				});
				frm.refresh_field("weighing_table_lgm");
			});
		}
	},

	refresh: function(frm) {
		if (frm.doc.docstatus === 1) {
			frm.add_custom_button(__('Create Job Card LGM'), function() {
				frm.call({
					method:"create_job_card_lgm",
					args:{
						doc:frm.doc
					},
					callback:function(r){
						frappe.msgprint({
							message: __('Job Card is created'),
							indicator: 'green'
						})
						return;
					},
				});
			});
		}
	},

	before_submit(frm){
		var ingredients_list = frm.doc["weighing_table_lgm"];
		var no_of_ingredients = frm.doc["weighing_table_lgm"].length;
		for (var i = 0; i < no_of_ingredients; i++){
			if (ingredients_list[i]["weighed"] == undefined){
				frm.reload_doc();
				frappe.throw({
					message: __(`Ingredient ${i+1} weight is not measured yet.`),
					indicator: 'red'
				});
			}
			if (!ingredients_list[i]["source_warehouse"]){
				frappe.throw({
					message: __(`Ingredient ${i+1} (${ingredients_list[i]["ingredient"]}) has no Source Warehouse set.`),
					indicator: 'red'
				});
			}
		}

		// before_submit must wait for these server round-trips to finish before
		// Frappe proceeds with the submit — same reasoning as the before_save fix.
		return frm.call({
			method: "check_stock_availability",
			args: { doc: frm.doc }
		}).then((r) => {
			var shortfalls = r.message || [];

			if (shortfalls.length === 0){
				// every ingredient has enough stock in its own source warehouse —
				// create the transfer straight away
				return frm.call({
					method: "create_material_transfer",
					args: { doc: frm.doc }
				}).then((r2) => {
					if (r2.message === false){
						frappe.throw({
							message: __('A Material Transfer for this Work Order already exists.'),
							indicator: 'red'
						});
					} else if (r2.message !== true){
						// stock changed between the two checks above and this
						// ingredient is short after all — rare, but don't
						// silently let submission through
						frappe.throw({
							message: __('Stock changed just now and is no longer sufficient. Please try submitting again.'),
							indicator: 'red'
						});
					}
				});
			}

			// one or more ingredients are short — ask a human to pick a fallback
			// warehouse for each one before the transfer is created
			return new Promise((resolve, reject) => {
				var dialog = new frappe.ui.Dialog({
					title: __('Insufficient Stock — Choose Fallback Warehouses'),
					fields: shortfalls.map((s, idx) => ({
						fieldname: 'warehouse_' + idx,
						fieldtype: 'Link',
						options: 'Warehouse',
						label: __(`${s.ingredient}: needs ${s.needed}, only ${s.available} in ${s.source_warehouse}`),
						reqd: 1
					})),
					primary_action_label: __('Confirm & Transfer'),
					primary_action: (values) => {
						var overrides = {};
						shortfalls.forEach((s, idx) => {
							overrides[s.ingredient] = values['warehouse_' + idx];
						});
						frm.call({
							method: "create_material_transfer",
							args: {
								doc: frm.doc,
								warehouse_overrides: overrides
							}
						}).then((r) => {
							if (r.message === true) {
								dialog.hide();
								resolve();
							} else if (r.message === false) {
								dialog.hide();
								reject(new Error(__('A Material Transfer for this Work Order already exists.')));
							} else {
								// still short — the chosen fallback warehouse(s)
								// don't have enough either. Keep the dialog open
								// and tell the human which ones, instead of
								// silently creating a transfer that would fail
								// or go negative.
								var still_short = (r.message || []).map((s) =>
									__(`${s.ingredient} in ${s.source_warehouse}: needs ${s.needed}, only ${s.available} available`)
								).join('<br>');
								frappe.msgprint({
									title: __('Still Insufficient'),
									message: __('The warehouse(s) you picked don\'t have enough stock either:') + '<br>' + still_short,
									indicator: 'red'
								});
							}
						}).catch(reject);
					}
				});
				dialog.get_close_btn().on('click', () => {
					reject(new Error(__('Submission cancelled — stock shortfalls were not resolved.')));
				});
				dialog.show();
			});
		});
	},
});


// child table 
frappe.ui.form.on('Ingredients Weighing Table LGM', {
    // cdt is Child DocType name i.e Quotation Item
    // cdn is the row name for e.g bbfcb8da6a
})