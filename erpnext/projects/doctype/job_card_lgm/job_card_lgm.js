// Copyright (c) 2024, Frappe Technologies Pvt. Ltd. and contributors
// For license information, please see license.txt

frappe.ui.form.on('Job Card LGM', {
	setup: function (frm) {
		frm.call({
			method: 'get_all_job_card',
			callback: function (r) {
				var sheet_name = r.message;
				// prevent duplicate work order of the same request sheet
				frm.set_query('work_order', function () {
					return {
						filters: {
							name: ['not in', sheet_name],
						},
					};
				});
			},
		});
	},

	refresh: function (frm) {
		frappe.flags.pause_job = 0;
		frappe.flags.resume_job = 0;

		if (frm.doc.work_order) {
			frm.trigger('for_quantity');
			frm.trigger('fetch_instructions');
		}

		if (
			frm.doc.docstatus == 0 &&
			(frm.doc.for_quantity > frm.doc.total_completed_qty ||
				!frm.doc.for_quantity) &&
			(frm.doc.ingredients ||
				!frm.doc.ingredients.length ||
				frm.doc.for_quantity == frm.doc.transferred_qty)
		) {
			frm.trigger('prepare_timer_buttons');
		}
	},

	onload: function (frm) {
		var df_type = frappe.meta.get_docfield(
			'Ingredients Weighing Table LGM',
			'ingredient_type',
			frm.doc.name,
		);
		if (df_type) {
			df_type.hidden = 1;
		}

		var df_warehouse = frappe.meta.get_docfield(
			'Ingredients Weighing Table LGM',
			'source_warehouse',
			frm.doc.name,
		);
		if (df_warehouse) {
			df_warehouse.read_only = 1;
		}
		frm.refresh_field('weighing_table_lgm');

		if (frm.doc.work_order) {
			frm.trigger('for_quantity');
			frm.trigger('fetch_instructions');
		}
	},

	before_save: function (frm) {
		frm.trigger('for_quantity');
	},

	on_submit: function (frm) {
		frm.trigger('for_quantity');
	},

	prepare_timer_buttons: function (frm) {
		frm.trigger('make_dashboard');
		if (!frm.doc.job_started) {
			frm.add_custom_button(__('Start'), () => {
				if (!frm.doc.employee) {
					frappe.prompt(
						{
							fieldtype: 'Link',
							label: __('Employee'),
							options: 'Employee',
							fieldname: 'employee',
						},
						(d) => {
							if (d.employee) {
								frm.set_value('employee', d.employee);
							} else {
								frm.events.start_job(frm);
							}
						},
						__('Enter Value'),
						__('Start'),
					);
				} else {
					frm.events.start_job(frm);
				}
			}).addClass('btn-primary');
		} else if (frm.doc.status == 'On Hold') {
			frm.add_custom_button(__('Resume'), () => {
				frappe.flags.resume_job = 1;
				frm.events.start_job(frm);
			}).addClass('btn-primary');
		} else {
			frm.add_custom_button(__('Pause'), () => {
				frappe.flags.pause_job = 1;
				frm.set_value('status', 'On Hold');
				frm.events.complete_job(frm);
			});

			frm.add_custom_button(__('Complete'), () => {
				let completed_time = frappe.datetime.now_datetime();
				frm.trigger('hide_timer');

				if (frm.doc.for_quantity) {
					frappe.prompt(
						{
							fieldtype: 'Float',
							label: __('Completed Quantity'),
							fieldname: 'qty',
							reqd: 1,
							default: frm.doc.for_quantity,
						},
						(data) => {
							frm.events.complete_job(frm, completed_time, data.qty);
						},
						__('Enter Value'),
						__('Complete'),
					);
				} else {
					frm.events.complete_job(frm, completed_time, 0);
				}
			}).addClass('btn-primary');
		}
	},

	start_job: function (frm) {
		let row = frappe.model.add_child(
			frm.doc,
			'Job Card Time Log',
			'time_logs',
		);
		row.from_time = frappe.datetime.now_datetime();
		frm.set_value('job_started', 1);
		frm.set_value('started_time', row.from_time);
		frm.set_value('status', 'Work In Progress');

		if (!frappe.flags.resume_job) {
			frm.set_value('current_time', 0);
		}

		frm.trigger('for_quantity');

		frm.save();
	},

	complete_job: function (frm, completed_time, completed_qty) {
		frm.doc.time_logs.forEach((d) => {
			if (d.from_time && !d.to_time) {
				d.to_time = completed_time || frappe.datetime.now_datetime();
				d.completed_qty = completed_qty || 0;

				if (frappe.flags.pause_job) {
					let currentIncrement =
						moment(d.to_time).diff(moment(d.from_time), 'seconds') || 0;
					frm.set_value(
						'current_time',
						currentIncrement + (frm.doc.current_time || 0),
					);
				} else {
					frm.set_value('started_time', '');
					frm.set_value('job_started', 0);
					frm.set_value('current_time', 0);
				}

				frm.save();
			}
		});
	},

	validate: function (frm) {
		if (
			(!frm.doc.time_logs || !frm.doc.time_logs.length) &&
			frm.doc.started_time
		) {
			frm.trigger('reset_timer');
		}
	},

	employee: function (frm) {
		if (frm.doc.job_started && !frm.doc.current_time) {
			frm.trigger('reset_timer');
		} else {
			frm.events.start_job(frm);
		}
	},

	reset_timer: function (frm) {
		frm.set_value('started_time', '');
		frm.set_value('job_started', 0);
		frm.set_value('current_time', 0);
	},

	make_dashboard: function (frm) {
		if (frm.doc.__islocal) return;

		frm.dashboard.refresh();
		const timer = `
			<div class="stopwatch" style="font-weight:bold;margin:0px 13px 0px 2px;
				color:#545454;font-size:18px;display:inline-block;vertical-align:text-bottom;>
				<span class="hours">00</span>
				<span class="colon">:</span>
				<span class="minutes">00</span>
				<span class="colon">:</span>
				<span class="seconds">00</span>
			</div>`;

		var section = frm.toolbar.page.add_inner_message(timer);

		let currentIncrement = frm.doc.current_time || 0;
		if (frm.doc.started_time || frm.doc.current_time) {
			if (frm.doc.status == 'On Hold') {
				updateStopwatch(currentIncrement);
			} else {
				currentIncrement += moment(frappe.datetime.now_datetime()).diff(
					moment(frm.doc.started_time),
					'seconds',
				);
				initialiseTimer();
			}

			function initialiseTimer() {
				const interval = setInterval(function () {
					var current = setCurrentIncrement();
					updateStopwatch(current);
				}, 1000);
			}

			function updateStopwatch(increment) {
				var hours = Math.floor(increment / 3600);
				var minutes = Math.floor((increment - hours * 3600) / 60);
				var seconds = increment - hours * 3600 - minutes * 60;

				$(section)
					.find('.hours')
					.text(hours < 10 ? '0' + hours.toString() : hours.toString());
				$(section)
					.find('.minutes')
					.text(
						minutes < 10 ? '0' + minutes.toString() : minutes.toString(),
					);
				$(section)
					.find('.seconds')
					.text(
						seconds < 10 ? '0' + seconds.toString() : seconds.toString(),
					);
			}

			function setCurrentIncrement() {
				currentIncrement += 1;
				return currentIncrement;
			}
		}
	},

	for_quantity: function (frm) {
		frm.call({
			method: 'get_ingredients',
			args: { doc: frm.doc },
			callback: function (r) {
				frm.clear_table('ingredients');
				(r.message || []).forEach((row) =>
					frm.add_child('ingredients', row),
				);
				frm.refresh_field('ingredients');
			},
		});
	},

	fetch_instructions: function (frm) {
		frm.call({
			method: 'get_instructions',
			args: { doc: frm.doc },
			callback: function (r) {
				frm.clear_table('mixing_cycle');
				(r.message || []).forEach((row) =>
					frm.add_child('mixing_cycle', row),
				);
				frm.refresh_field('mixing_cycle');
			},
		});
	},

	hide_timer: function (frm) {
		frm.toolbar.page.inner_toolbar.find('.stopwatch').remove();
	},

	timer: function (frm) {
		return `<button> Start </button>`;
	},

	set_total_completed_qty: function (frm) {
		frm.doc.total_completed_qty = 0;
		frm.doc.time_logs.forEach((d) => {
			if (d.completed_qty) {
				frm.doc.total_completed_qty += d.completed_qty;
			}
		});

		refresh_field('total_completed_qty');
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
				method: 'create_material_issue',
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
						title: __('ERROR: Insufficient Stock'),
						fields: [
							{
								fieldname: 'shortfall_info',
								fieldtype: 'HTML',
								options: `<p>Something went wrong, the following ingredients lack sufficient stock:</p>
									<ul>${shortfall_html}</ul>
									<p>Please initiate a material transfer to correct the stock amount.</p>`,
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
									'Submission cancelled - Please initiate a material transfer so that the number of materials is correct.',
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

frappe.ui.form.on('Job Card Time Log', {
	completed_qty: function (frm) {
		frm.events.set_total_completed_qty(frm);
	},

	to_time: function (frm) {
		frm.set_value('job_started', 0);
		frm.set_value('started_time', '');
	},
});
