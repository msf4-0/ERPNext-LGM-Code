# -*- coding: utf-8 -*-
# Copyright (c) 2023, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

from __future__ import unicode_literals
import frappe, json
from frappe.model.document import Document
from frappe import _
from frappe.utils import flt

class WorkOrderLGM(Document):
	def before_cancel(self):
		self.ignore_linked_doctypes = ["Stock Entry", "Job Card LGM"]

	def on_cancel(self):
		reclaim_unused_job_card_materials(self.name)

def reclaim_unused_job_card_materials(work_order_name):
	UNSUBMITTED_STATUS_ENUM = 0
	SUBMITTED_STATUS_ENUM = 1
	CANCELLED_STATUS_ENUM = 2

	work_order_doc = frappe.get_doc("Work Order LGM", work_order_name)
	ingredients_by_type = get_ingredients(work_order_doc)

	wo_job_cards = frappe.get_all(
		"Job Card LGM", 
		filters = {"work_order": work_order_name}, 
		fields = ["name", "docstatus", "ingredient_type"]
		)
	job_card_by_type = {jc.ingredient_type: jc for jc in wo_job_cards}

	weights = {}

	for ingredient_type, rows in ingredients_by_type.items():
		jc = job_card_by_type.get(ingredient_type)

		if jc and jc.docstatus in (SUBMITTED_STATUS_ENUM, CANCELLED_STATUS_ENUM):
			continue

		if jc and jc.docstatus == UNSUBMITTED_STATUS_ENUM:
			job_card_doc = frappe.get_doc("Job Card LGM", jc.name)
			for row in job_card_doc.ingredients:
				if row.ingredient_weight:
					weights[row.ingredient] = weights.get(row.ingredient, 0) 
					+ flt(row.ingredient_weight)

			continue

		for row in rows:
			if row.get("ingredient_weight"):
				weights[row["ingredient"]] = weights.get(row["ingredient"], 0) + flt(row["ingredient_weight"])

	if not weights:
		return

	wip = _get_wip_warehouse()
	unused_wip = _get_unused_wip_warehouse()

	if not wip:
		frappe.throw(_("Cannot find WIP warehouse"))
	if not wip:
			frappe.throw(_("Cannot find Unused Work In Progress warehouse"))

	stock_entry_details = [
		dict(s_warehouse = wip, t_warehouse = unused_wip, item_code = ingredient_name, qty=qty) 
		for ingredient_name, qty in weights.items()
	]

	stock_entry = frappe.get_doc(dict(
		doctype = "Stock Entry",
		stock_entry_type = "Material Transfer",
		work_order_lgm = work_order_name,
		items = stock_entry_details,
	)).insert()
	stock_entry.submit()
	
def _get_wip_warehouse():
	warehouses = frappe.get_all("Warehouse", fields="name")
	wip = None
	for warehouse in warehouses:
		if "Work In" in warehouse["name"] and "Unused" not in warehouse["name"]:
			wip = warehouse["name"]

	return wip

def _get_unused_wip_warehouse():
	warehouse_name = "Unused Work In Progress"

	warehouses = frappe.get_all("Warehouse", filters = {"name": ["like", "%" + warehouse_name + "%"]}, fields="name")
	if not warehouses:
		frappe.throw(_('No warehouse matching name {0} was found').format(warehouse_name))

	unused_wip = warehouses[0]["name"]

	return unused_wip

@frappe.whitelist(allow_guest=True)
def get_weight_from_nodered():
	data = json.loads(frappe.request.data)
	order_no = data["work"]
	weight = data["weight"]
	mixer_no = data["mixer"]
	ingredient_name = data["name"]
	try:
		doc = frappe.get_doc("Work Order LGM", "Work-Order-" + str(order_no))
	except:
		frappe.throw("Work Order does not exist")
	ingredient_list = doc.weighing_table_lgm
	for ingredient in ingredient_list:
		if ingredient.ingredient == ingredient_name and ingredient.mixer_no == mixer_no:
			ingredient.weighed = weight
			doc.save()
			doc.reload()
			return "found"
	return "not found"

def build_ingredient_row(row_data):
	"""
	Single source of truth for the row shape used both when Job Cards are first
	created (grouped by ingredient_type, from the Work Order's own child table)
	and when a Job Card's form re-syncs against the live Work Order
	(job_card_lgm.get_ingredients). Works with either a plain dict (client-sent
	doc) or a live Frappe child-table row, since both support .get().
	"""
	return {
		"ingredient": row_data.get("ingredient"),
		"ingredient_weight": row_data.get("ingredient_weight"),
		"mixer_no": row_data.get("mixer_no"),
		"weighed": row_data.get("weighed"),
		"source_warehouse": row_data.get("source_warehouse"),
	}

def get_ingredients(doc):
	"""
	Returns a dict of ingredient lists, grouped by ingredient_type.
	"""
	obj = doc.get("weighing_table_lgm", [])
	output = {}

	for row_data in obj:
		ingredient_type = row_data.get("ingredient_type")
		if ingredient_type not in output:
			output[ingredient_type] = []
		output[ingredient_type].append(build_ingredient_row(row_data))

	return output

@frappe.whitelist()
def create_job_card_lgm(doc):
    """
    Creates Job Card LGM documents based on values filled in the doc.
    One job card will be created per ingredient type.
    """
    doc = json.loads(doc)
    ingredients_dict = get_ingredients(doc)

    technological_request_sheet = frappe.get_doc("Technological Request Sheets LGM", doc["request_sheet_link"])
    workstation_request_sheet = technological_request_sheet.factory_reference_no
    mixing_instruction = technological_request_sheet.mixing_cycle

    # Rebuild the list as dictionaries to prevent parent theft
    new_mixing_cycle = []
    for instruction in mixing_instruction:
        new_mixing_cycle.append({
            "mixing_time": instruction.mixing_time,
            "mixing_process": instruction.mixing_process
        })

    job_cards = []
    created_count = 0
    valid_types_count = 0 # Track valid attempts to prevent failing on empty payloads

    for ingredient_type, ingredients_list in ingredients_dict.items():
        if not ingredient_type:
            frappe.throw(_("Ingredient type not present for ingredients {0}".format(ingredients_list)))

        if not ingredients_list:
            continue
            
        valid_types_count += 1

        # Skip gracefully instead of aborting the entire process
        if frappe.db.exists("Job Card LGM", {
            "work_order": doc.get("name"),
            "ingredient_type": ingredient_type
        }):
            continue 

        job_card_lgm = frappe.get_doc(dict(
            doctype = 'Job Card LGM',
            work_order = doc["name"],
            request_sheet = doc["request_sheet_link"],
            workstation = workstation_request_sheet,
            for_quantity = 1,
            ingredient_type = ingredient_type,
            ingredients = ingredients_list,
            mixing_cycle = new_mixing_cycle
        ))

        job_cards.append(job_card_lgm)
        created_count += 1

    # Only throw if we had valid items to process, but none were actually created
    if valid_types_count > 0 and created_count == 0:
        frappe.throw(_("All Job Cards for Work Order {0} already exist.").format(doc.get("name")))

    for job_card_lgm in job_cards:
        job_card_lgm.insert()

    return True

def _get_stock_shortfalls(weights):
	"""
	Returns a list of stocks that have shortfalls (insufficient stock).
	"""
	shortfalls = []
	for (ingredient_name, source_warehouse), needed_qty in weights.items():
		bin_qty = frappe.get_value(
			"Bin",
			{"item_code": ingredient_name, "warehouse": source_warehouse},
			"actual_qty"
		) or 0

		if bin_qty < needed_qty:
			shortfalls.append({
				"ingredient": ingredient_name,
				"source_warehouse": source_warehouse,
				"needed": needed_qty,
				"available": bin_qty
			})

	return shortfalls

# function to create the Material Transfer stock entry for a work order.
# warehouse_overrides is an optional {ingredient: fallback_warehouse} map —
# populated by the user when check_stock_availability found that ingredient's
# normal source_warehouse short, and they picked a different warehouse to
# pull from instead. Any ingredient not in the map uses its own row-level
# source_warehouse as usual.
@frappe.whitelist()
def create_material_transfer(doc, warehouse_overrides=None):
	doc = json.loads(doc)
	if warehouse_overrides:
		if isinstance(warehouse_overrides, str):
			warehouse_overrides = json.loads(warehouse_overrides)
	else:
		warehouse_overrides = {}

	ingredient_list = doc.get("weighing_table_lgm", [])
	weights = {}
	# summing up the weights based on (ingredient, source_warehouse) — the
	# override, if the human picked a fallback for this ingredient, wins over
	# the row's own source_warehouse
	for row in ingredient_list:
		ingredient_name = row["ingredient"]
		source_warehouse = warehouse_overrides.get(ingredient_name) or row.get("source_warehouse")
		if not source_warehouse:
			frappe.throw(_("Source Warehouse is not set for ingredient {0}").format(ingredient_name))

		if row.get("ingredient_weight"):
			key = (ingredient_name, source_warehouse)
			weights[key] = weights.get(key, 0) + float(row["ingredient_weight"])

	if not weights:
		return True

	# re-check availability against the *final resolved* warehouse for each
	# ingredient (its own source_warehouse, or the human-provided override).
	# This is necessary because the earlier check_stock_availability call only
	# ever validated each row's original source_warehouse — it has no way of
	# knowing whether a fallback warehouse the human just picked is itself
	# short. Same shape of result as check_stock_availability, so the caller
	# can reuse the same dialog logic if this comes back non-empty.
	shortfalls = _get_stock_shortfalls(weights)
	if shortfalls:
		return shortfalls

	wip = _get_wip_warehouse()

	# put each (ingredient, source_warehouse) pair and its total weight into
	# the stock entry's item rows
	stock_entry_details = []
	for (ingredient_name, source_warehouse), ingredient_weight in weights.items():
		stock_entry_details.append(dict(
			s_warehouse = source_warehouse,
			t_warehouse = wip,
			item_code = ingredient_name,
			qty = ingredient_weight
		))

	# insert stock entry record here — no single header-level from_warehouse
	# any more, since source warehouses now differ per row
	stock_entry = frappe.get_doc(dict(
		doctype = "Stock Entry",
		stock_entry_type = "Material Transfer",
		work_order_lgm = doc["name"],
		to_warehouse = wip,
		items = stock_entry_details,
	)).insert()
	stock_entry.submit()
	return True

# function to query all the request sheet that has no work order
@frappe.whitelist()
def get_all_work_order():
	data = frappe.get_all("Work Order LGM", fields="request_sheet_link")
	output = []
	for forms in data:
		if forms.request_sheet_link not in output:
			output.append(forms.request_sheet_link)
	return output

def update_status(work_order_name):
	"""
	Updates the Work Order's status based on the status of its job cards.

	Rules:
	- Started <- No non-cancelled job cards exist, or job cards are open
	- Completed <- Every non-cancelled job card is completed
	- In progress <- Everything else
	"""
	CANCELLED_STATUS_NUM = 2

	job_cards = frappe.get_all(
		"Job Card LGM",
		filters = {"work_order": work_order_name, "docstatus": ["!=", CANCELLED_STATUS_NUM]},
		fields = ["status"]
	)

	if not job_cards or all(jc.status == "Open" for jc in job_cards):
		new_status = "Not Started"
	elif all(jc.status == "Completed" for jc in job_cards):
		new_status = "Completed"
	else:
		new_status = "In Progress"

	frappe.db.set_value("Work Order LGM", work_order_name, "status", new_status)

	frappe.publish_realtime(
		event="work_order_lgm_status_changed",
		message={"work_order": work_order_name, "status": new_status}
	)

	
