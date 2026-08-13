# -*- coding: utf-8 -*-
# Copyright (c) 2023, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

from __future__ import unicode_literals
import math
import frappe, json
from frappe.model.document import Document
from frappe import _
from frappe.utils import flt

class WorkOrderLGM(Document):
	pass

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


@frappe.whitelist()
def create_job_card_lgm(doc):
	# parse to json object
	doc = json.loads(doc)
	# check if work order that is linked to the current request sheet already exists
	if len(frappe.db.get_all('Job Card LGM', fields="work_order", filters={"work_order": doc["name"]})) > 0:
		frappe.throw(_("Job Card for current work order already exists."))
	else:
		# get ingredients
		ingredients_lists = get_ingredients(doc)

		workstation_request_sheet = frappe.get_doc("Technological Request Sheets LGM", doc["request_sheet_link"]).factory_reference_no
		mixing_instruction = frappe.get_doc("Technological Request Sheets LGM", doc["request_sheet_link"]).mixing_cycle
		counter = 1
		# populate child table 
		for mixer in ingredients_lists:
			# insert record
			job_card_lgm = frappe.get_doc(dict(
				doctype='Job Card LGM',
				work_order = doc["name"],
				request_sheet=doc["request_sheet_link"],
				workstation = workstation_request_sheet,
				for_quantity=1,
				mixer_no_job_card=counter,
				ingredients=mixer,
				mixing_cycle=mixing_instruction
			)).insert()

			job_card_lgm.save()
			counter += 1

	return True

def get_ingredients(doc):
	obj = doc["weighing_table_lgm"]
	no_of_mixer = 0
	initial_mixer = None
	for data in obj:
		if initial_mixer is None:
			initial_mixer = int(data["mixer_no"])
			no_of_mixer += 1
		else:
			if int(data["mixer_no"]) == initial_mixer:
				break
			else:
				no_of_mixer += 1

	output = [[] for _ in range (no_of_mixer)]
	for data in obj:
		output[int(data["mixer_no"])-1].append({
			"ingredient": data["ingredient"],
			"ingredient_weight": data["weighed"],
			"mixer_no": data["mixer_no"],
			"weighed": data["weighed"],
		})
	return output


@frappe.whitelist()
def create_work_order_lgm(doc):
	# parse to json object
	doc = json.loads(doc)

	request_sheet_doc = frappe.get_doc("Technological Request Sheets LGM", doc["request_sheet_link"])
	# get ingredients
	ingredients_lists = get_ingredients_from_request_sheet(request_sheet_doc)

	# populate child table 
	table_list = []
	for ingredient in ingredients_lists:
		for ingredient_details in ingredient:
			table_list.append(
				{
					"ingredient": ingredient_details[0],
					"ingredient_weight": ingredient_details[1],
					"mixer_no": ingredient_details[2],
					"source_warehouse": None
				}
			)

	# insert record
	doc["weighing_table_lgm"] = table_list
	return doc

def get_ingredients_from_request_sheet(doc):
	# get ingredients from commpounding ingredients child table
	ingredient_list = []
	compounding_list_object = doc.compounding_ingredients
	for list_object in compounding_list_object:
		mixer_no = int(list_object.select_mixer_no)
		ingredient_name = list_object.ingredient
		if ingredient_name != "Masterbatch":
			ingredient = []
			for i in range (1, mixer_no+1):
				if getattr(list_object,"mixer_" + str(i)) is not None:
					ingredient_weight = getattr(list_object,"mixer_" + str(i))
					ingredient.append((ingredient_name, ingredient_weight, i))
			ingredient_list.append(ingredient)

	# get ingredients from curing ingredients child table
	curing_list_object = doc.curing_ingredients
	for list_object in curing_list_object:
		mixer_no = int(list_object.select_mixer_no)
		ingredient_name = list_object.ingredient
		if ingredient_name != "Masterbatch":
			ingredient = []
			for i in range (1, mixer_no+1):
				if getattr(list_object,"mixer_" + str(i)) is not None:
					ingredient_weight = getattr(list_object,"mixer_" + str(i))
					ingredient.append((ingredient_name, ingredient_weight, i))
			ingredient_list.append(ingredient)
	return ingredient_list

# function to check whether every ingredient's own source_warehouse has enough
# on-hand quantity before a Material Transfer is created.
# Groups by (ingredient, source_warehouse) rather than just ingredient, because
# the same ingredient can now legitimately be sourced from two different
# warehouses across two rows (e.g. two different mixers).
# Returns a list of shortfalls (empty list = everything is sufficient), each:
#   {"ingredient": ..., "source_warehouse": ..., "needed": ..., "available": ...}
@frappe.whitelist()
def check_stock_availability(doc):
	doc = json.loads(doc)
	ingredient_list = doc["weighing_table_lgm"]

	# sum up the required weight per (ingredient, source_warehouse) pair,
	# since the same pair can appear on multiple rows (e.g. different mixers)
	weights = {}
	for row in ingredient_list:
		ingredient_name = row["ingredient"]
		source_warehouse = row.get("source_warehouse")
		if not source_warehouse:
			frappe.throw(_("Source Warehouse is not set for ingredient {0}").format(ingredient_name))
		key = (ingredient_name, source_warehouse)
		weights[key] = weights.get(key, 0) + float(row["weighed"])

	# check each (ingredient, source_warehouse) pair's actual on-hand quantity
	# via the Bin doctype, which is where ERPNext/Frappe tracks per-item,
	# per-warehouse stock levels
	shortfalls = []
	for (ingredient_name, source_warehouse), needed_qty in weights.items():
		bin_qty = _get_bin_qty(ingredient_name, source_warehouse)
		if bin_qty < needed_qty:
			_add_shortfall(ingredient_name, source_warehouse, 
				  needed_qty, bin_qty, shortfalls)

	return shortfalls

def _get_bin_qty(ingredient_name: str, warehouse_name: str) -> int:
    """
    Gets the quantity (bin) of an ingredient based on its name and warehouse.
	(0 if item not in warehouse)
	
	Returns:
        int: Quantity found for the ingredient in the warehouse
    """
    bin_qty = frappe.db.get_value(
        "Bin",
        {"item_code": ingredient_name, "warehouse": warehouse_name}, "actual_qty"
        ) or 0

    return bin_qty

def _get_bag_uom_conversion_factor(item_docname: str) -> float:
	"""
	Gets conversion factor of weight per "Bag" UOM for selected item.
	
	Weight unit (g/kg) depends on conversion unit as defined in the item master.
	
	Returns:
		float: Conversion factor of weight per "Bag" UOM

	Raises:
		frappe.throw: If item does not have a UOM called "Bag" (case sensitive) in item master.
	"""
	UOM = "Bag"

	conversion_factor = frappe.db.get_value(
		"UOM Conversion Detail",
		{"parent": item_docname, "uom": UOM},
		"conversion_factor"
	)

	if not conversion_factor:
		frappe.throw(_(
			"No {0} UOM conversion factor set on item {1}."
			"Add a {0} row with a UOM conversion factor in the Item master (Item page) before submitting."
		).format(UOM, item_docname))

	return flt(conversion_factor)

def _add_shortfall(ingredient_docname: str, source_warehouse_docname: str, 
				  needed_qty: float, available_qty: float, shortfalls: list[dict]):
	"""
	Appends an ingredient's name, source warehouse, needed and available quantities, to the shortfalls list.
	
	Returns:
		None: Only appends a dict containing ingredient details to provided shortfalls list.
	"""
	shortfalls.append({
		"ingredient": ingredient_docname,
		"source_warehouse": source_warehouse_docname,
		"needed": needed_qty,
		"available": available_qty
	})

def _get_ingredient_weights(doc: dict) -> dict:
	"""
	Returns a dict with weights of all ingredient-warehouse pairs submitted.

	Args:
		doc (dict): Dict of document, after using json.loads()

	Returns:
		dict:
			Key = (ingredient_docname, source_warehouse_docname): tuple(str, str)
			Value = Weight of combination: float
	"""
	ingredient_list = doc["weighing_table_lgm"]
	weights = {}

	for row in ingredient_list:
		ingredient_docname = row["ingredient"]
		source_whouse_docname = row.get("source_warehouse")
		if not source_whouse_docname:
			frappe.throw(_("Source Warehouse is not set for ingredient {0}").format(ingredient_docname))
		key = (ingredient_docname, source_whouse_docname)
		weights[key] = weights.get(key, 0) + float(row["weighed"])

def _create_and_submit_material_transfer(work_order_name: str, items: list[dict], to_warehouse_docname: str = None):
	"""
	Creates, submits, and returns a material transfer.

	Args:
		work_order_name (str) = Name of work order
		items (list[dict]) = Individual items/entries in the stock entry
		to_warehouse_docname (str) = Docname of the target warehouse (If it is the destination of all items)
	
	Returns:
		Material transfer stock entry that was created
	"""
	entry_dict = dict(
			doctype = "Stock Entry",
			stock_entry_type = "Material Transfer",
			work_order_lgm = work_order_name,
			items = items,
		)
	if to_warehouse_docname:
		entry_dict["to_warehouse"] = to_warehouse_docname

	stock_entry = frappe.get_doc(entry_dict).insert()
	stock_entry.submit()
	return stock_entry

def _handle_replenishment(doc: dict, weights: dict, warehouse_overrides: dict) -> list[dict]|None:
	"""
	Handles replenishment of ingredients, based on provided weights and warehouse overrides.

	Determines if source and fallback warehouses have enough ingredients.

	If ALL ingredients are sufficient but requires a replenishment transfer,
	a replenishment transfer will be automatically made for all needed ingredients.

	If ANY ingredients are insufficient even after a fallback warehouse,
	no transfers will be made and a list of ingredient dicts with shortfalls
	will be returned.

	Args:
		doc (dict): 
		weights (dict): Dict of document, after using json.loads()
			Key = (ingredient_docname, source_warehouse_docname): tuple(str, str)
			Value = Weight of combination: float)
		warehouse_overrides (dict):
			Key = ingredient_docname: str
			Value = fallback_warehouse_docname: str

	Returns:
		list[dict]|None:
			List of ingredient dicts with shortfalls
	"""
	shortfalls = []
	replenish_rows = []

	for (ingredient_docname, source_whouse_docname), needed_weight in weights.items():
		bin_weight = _get_bin_qty(ingredient_docname, source_whouse_docname)
		
		if bin_weight >= needed_weight:
			continue	# Source warehouse's ingredients suffice

		# Not enough ingredients in source warehouse, get ingredient's fallback warehouse
		fallback_whouse_docname = warehouse_overrides.get(ingredient_docname)
		if not fallback_whouse_docname:
			_add_shortfall(ingredient_docname, source_whouse_docname, 
				  needed_weight, bin_weight, shortfalls)
			continue

		# Fallback warehouse found, determine if replenish amount is sufficient
		shortage_weight = needed_weight - bin_weight
		bag_weight_conversion_factor = _get_bag_uom_conversion_factor(ingredient_docname)
		shortage_bags = math.ceil(shortage_weight / bag_weight_conversion_factor)
		replenish_weight = shortage_bags * bag_weight_conversion_factor

		fallback_weight = _get_bin_qty(ingredient_docname, fallback_whouse_docname)
		if fallback_weight < replenish_weight:
			_add_shortfall(ingredient_docname, fallback_whouse_docname, replenish_weight, 
				  fallback_weight, shortfalls)
			continue

		# Add ingredient details to entries for replenishment stock entry for later
		replenish_rows.append(dict(
			s_warehouse = fallback_whouse_docname,
			t_warehouse = source_whouse_docname,
			item_code = ingredient_docname,
			qty = fallback_weight
		))

	if shortfalls:
		return shortfalls

	if replenish_rows:
		_create_and_submit_material_transfer(doc["name"], replenish_rows)


@frappe.whitelist()
def create_material_transfer(doc, warehouse_overrides = None):
	"""
	Creates the material transfer entry upon work order submission.

	Args:
		doc: Serialised doc.
		warehouse_overrides: Serialised dict, Key: Ingredient Docname, 
			Value: Fallback Warehouse Docname.
		
	Returns:
		bool:
			True: Success.
			False: Aborted - duplicate material transfer exists.
		list[dict]:
			Aborted - list of material shortfalls, even after fallback warehouses.
	"""
	doc = json.loads(doc)
	if warehouse_overrides:
		if isinstance(warehouse_overrides, str):
			warehouse_overrides = json.loads(warehouse_overrides)
	else:
		warehouse_overrides = {}

	warehouses = frappe.get_all("Warehouse", fields="name")
	wip_whouse_docname = None
	for warehouse in warehouses:
		if "Work In" in warehouse["name"]:
			wip_whouse_docname = warehouse["name"]

	# if a Material Transfer stock entry already exists for this work order,
	# do not create a duplicate — return False so the caller can stop and warn
	existing = frappe.get_list(
		"Stock Entry",
		fields="name",
		filters=[
			["Stock Entry", "work_order_lgm", "=", doc["name"]],
			["Stock Entry", "stock_entry_type", "=", "Material Transfer"],
			["Stock Entry Detail", "t_warehouse", "=", wip_whouse_docname],
		]
	)
	if len(existing) > 0:
		return False

	weights = _get_ingredient_weights(doc)

	shortfalls = _handle_replenishment(doc, weights, warehouse_overrides)
	if shortfalls:
		return shortfalls

	# put each (ingredient, source_warehouse) pair and its total weight into
	# the stock entry's item rows
	stock_entry_details = []
	for (ingredient_docname, source_whouse_docname), ingredient_weight in weights.items():
		stock_entry_details.append(dict(
			s_warehouse = source_whouse_docname,
			t_warehouse = wip_whouse_docname,
			item_code = ingredient_docname,
			qty = ingredient_weight
		))

	_create_and_submit_material_transfer(doc["name"], stock_entry_details, wip_whouse_docname)
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