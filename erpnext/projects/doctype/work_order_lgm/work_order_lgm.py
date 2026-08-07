# -*- coding: utf-8 -*-
# Copyright (c) 2023, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

from __future__ import unicode_literals
import frappe, json
from frappe.model.document import Document
from frappe import _

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
					"mixer_no": ingredient_details[2]
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

# function to check if stock entry already exist for current work order
@frappe.whitelist()
def check_stock_entry(doc):
	doc = json.loads(doc)

	# if a Material Transfer stock entry already exists for this work order,
	# do not create a duplicate — return False so the caller can stop and warn
	existing = frappe.get_list(
		"Stock Entry",
		fields="name",
		filters={"work_order_lgm": doc["name"], "stock_entry_type": "Material Transfer"}
	)
	if len(existing) > 0:
		return False

	ingredient_list = doc["weighing_table_lgm"]
	weights = {}
	# summing up the weights based on the ingredients
	# get total weight of the ingredients in a work order 
	for i in range (len(ingredient_list)):
		ingredient_name = ingredient_list[i]["ingredient"]
		ingredient_weight = float(ingredient_list[i]["weighed"])
		if weights.get(ingredient_name) is None:
			weights[ingredient_name] = ingredient_weight
		else:
			ori_weight = weights[ingredient_name]
			weights[ingredient_name] = ori_weight + ingredient_weight

	# create stock entry
	stock_entry_details = []
	warehouses = frappe.get_all("Warehouse", fields="name")
	stores = None
	wip = None
	# get wip and stores warehouses
	for warehouse in warehouses:
		if "Stores" in warehouse["name"]:
			stores = warehouse["name"]
		elif "Work In" in warehouse["name"]:
			wip = warehouse["name"]

	# put all ingredients and ingredient weights into a list
	for keys in weights.items():
		ingredient_name = keys[0]
		ingredient_weight = keys[1]
		stock_entry_detail = dict(
			s_warehouse = stores,
			t_warehouse = wip,
			item_code = ingredient_name,
			qty = ingredient_weight
		)
		stock_entry_details.append(stock_entry_detail)

	# isnert stock entry record here
	stock_entry = frappe.get_doc(dict(
		doctype = "Stock Entry",
		stock_entry_type = "Material Transfer",
		work_order_lgm = doc["name"],
		from_warehouse = stores,
		to_warehouse = wip,
		items = stock_entry_details,
	)).insert()
	stock_entry.save()
	stock_entry.submit()
	return True

# function to check whether the Stores warehouse has enough on-hand quantity
# of every ingredient in the weighing table before a Material Transfer is created.
# Kept separate from check_stock_entry (which only guards against duplicate
# stock entries) because these are two independent conditions:
#   - check_stock_entry:      "has this already been transferred?"
#   - check_stock_availability: "is there enough stock to transfer?"
# Returns:
#   - the name of the first ingredient found short on stock (str), or
#   - True if every ingredient has sufficient stock
@frappe.whitelist()
def check_stock_availability(doc):
	doc = json.loads(doc)
	ingredient_list = doc["weighing_table_lgm"]

	# sum up the required weight per ingredient, same as check_stock_entry does,
	# since the same ingredient can appear on multiple rows (e.g. from different stages)
	weights = {}
	for i in range(len(ingredient_list)):
		ingredient_name = ingredient_list[i]["ingredient"]
		ingredient_weight = float(ingredient_list[i]["weighed"])
		if weights.get(ingredient_name) is None:
			weights[ingredient_name] = ingredient_weight
		else:
			weights[ingredient_name] += ingredient_weight

	# find the Stores warehouse the same way check_stock_entry does
	warehouses = frappe.get_all("Warehouse", fields="name")
	stores = None
	for warehouse in warehouses:
		if "Stores" in warehouse["name"]:
			stores = warehouse["name"]
			break

	# check each ingredient's actual on-hand quantity in Stores via the Bin doctype,
	# which is where ERPNext/Frappe tracks per-item, per-warehouse stock levels
	for ingredient_name, needed_qty in weights.items():
		bin_qty = frappe.db.get_value(
			"Bin",
			{"item_code": ingredient_name, "warehouse": stores},
			"actual_qty"
		) or 0
		if bin_qty < needed_qty:
			return ingredient_name

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