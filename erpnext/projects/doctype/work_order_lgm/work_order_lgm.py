# -*- coding: utf-8 -*-
# Copyright (c) 2023, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

from __future__ import unicode_literals
import frappe, json
from frappe.model.document import Document
from frappe import _
from frappe.integrations.utils import make_post_request

class WorkOrderLGM(Document):
	pass

# a function to send a signal to node red to get the current weight on the weighing scale
@frappe.whitelist()
def trigger_nodered(doc, cdt, cdn):
	doc = json.loads(doc)
	raw_url = frappe.get_doc("LGM Settings").lgm_url 
	raw_port = frappe.get_doc("LGM Settings").lgm_port
	token_key = frappe.get_doc("LGM Settings").token_key
	secret_key = frappe.get_doc("LGM Settings").secret_key
	url = raw_url + ":" + raw_port + "/listen"
	headers = {
		'Content-type': 'application/json',
	}
	data = json.dumps({
		"url": raw_url,
		"doc": doc,
		"cdt": cdt,
		"cdn": cdn,
		"token": token_key,
		"secret": secret_key
	})
	resp = make_post_request(url, headers=headers, data=data)
	return resp

# function to receive the weight from node red and set it to the row that clicked the button
@frappe.whitelist(allow_guest=True)
def get_data_from_nodered():
	data = json.loads(frappe.request.data)
	doc_name = data["doc"]["name"]
	cdt = data["cdt"]
	cdn = data["cdn"]
	weight = data["weight"]
	doc = frappe.get_doc("Work Order LGM", doc_name)
	ingredient_list = doc.weighing_table_lgm
	for ingredient in ingredient_list:
		if ingredient.name == cdn:
			ingredient.weighed = weight
			doc.save()
			doc.reload()
			return True
	return False

# function to automatically create the job cards for current work order
@frappe.whitelist()
def create_job_card_lgm(doc):
	# parse to json object
	doc = json.loads(doc)
	# check if work order that is linked to the current request sheet already exists
	if len(frappe.db.get_all('Job Card LGM', fields="work_order", filters={"work_order": doc["name"]})) > 0:
		frappe.throw(_("Job Card for current work order already exists."))
	else:
		# get ingredients
		stages_ingredient_list = get_ingredients_per_stage(doc)
		# for each stage and eaceh recipe, create a job card
		for stage_doc in stages_ingredient_list:
			stage = stage_doc[0]
			mixer_no = stage_doc[1]
			ingredient_list = stage_doc[2]
			mixing_instruction = frappe.get_doc("Stages LGM", stage).mixing_cycle

			job_card_lgm = frappe.get_doc(dict(
				doctype='Job Card LGM',
				work_order = doc["name"],
				request_sheet=doc["request_sheet_link"],
				for_quantity=1,
				mixer_no_job_card=mixer_no,
				ingredients=ingredient_list,
				mixing_cycle=mixing_instruction,
				stage=stage
			)).insert()
			job_card_lgm.save()
			# print(job_card_lgm.ingredients)

	return True

# function to get all ingredients and sort it in a stage and a recipe
def get_ingredients_per_stage(doc):
	stages = query_stages(doc)
	output = []
	# for each stage
	for stage in stages:
		stage_doc = frappe.get_doc("Stages LGM", stage[1])
		# for each recipe
		for k in range (1, 16):
			ingredient_list_per_stage = stage_doc.get("batch_weight_lgm_" + str(k))
			if len(ingredient_list_per_stage) > 0:
				stage_recipe_ingredients = []
				# for each row in a recipe
				for ingredient in ingredient_list_per_stage:
					if "RS-" in ingredient.ingredient:
						if int(stage_doc.is_first_stage) != 1:
							stage_recipe_ingredients.append({
								"mixer_no": ingredient.mixer_no,
								"ingredient": ingredient.ingredient,
								"ingredient_weight": ingredient.ingredient_weight,
							})
					else:
						stage_recipe_ingredients.append({
								"mixer_no": ingredient.mixer_no,
								"ingredient": ingredient.ingredient,
								"ingredient_weight": ingredient.ingredient_weight,
							})
				output.append((stage[1], k, stage_recipe_ingredients))
	return output

# function to get the ingredients from the request sheet 
# function is only called if the work order is not created from request sheet,
# rather it is created from the work order list
@frappe.whitelist()
def create_work_order_lgm(doc):
	# parse to json object
	doc = json.loads(doc)

	# get ingredients
	ingredients_lists = get_ingredients_from_stages(doc)

	# populate child table 
	table_list = []
	for ingredient in ingredients_lists:
		table_list.append(
			{
				"mixer_no": ingredient[0],
				"ingredient": ingredient[1],
				"ingredient_weight": ingredient[2],
			}
		)

	# insert record
	doc["weighing_table_lgm"] = table_list
	return doc

# function to get all ingredients from all the stages in a request sheet
def get_ingredients_from_stages(doc):
	# get ingredients from commpounding ingredients child table
	ingredient_list = []
	stages = query_stages(doc)
	# for each stage
	for stage in stages:
		stage_object = frappe.get_doc("Stages LGM", stage[1])
		# for each recipe
		for i in range (1,16):
			batch_weight_lgm = stage_object.get("batch_weight_lgm_" + str(i))
			# for each row in a recipe
			for row in batch_weight_lgm:
				recipe_no = row.get("mixer_no")
				ingredient_name = row.get("ingredient")
				ingredient_weight = row.get("ingredient_weight")
				if "RS-" not in ingredient_name:
					ingredient_list.append((recipe_no, ingredient_name, ingredient_weight))
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

# function to query and sort all stages in correct order
def query_stages(doc):
	stages = []
	request_sheet = doc["request_sheet_link"]
	for i in range (0,5):
		if len(stages) == 0:
			first_stage = frappe.get_list("Stages LGM", filters={'request_sheet_link': request_sheet, 'is_first_stage': 1})
			if len(first_stage) > 0:
				stages.append(("stage_"+str(i+1),first_stage[0]["name"]))
		else:
			next_stage = frappe.get_list("Stages LGM", filters={'previous_stage_link': stages[i-1][1]})
			if len(next_stage) > 0:
				stages.append(("stage_"+str(i+1), next_stage[0]["name"]))
			else:
				break

	# callers of this function (create_work_order_lgm, create_job_card_lgm) assume
	# stages already exist for this Request Sheet, so an empty result here means
	# something's wrong upstream — fail loudly instead of silently creating an
	# empty Work Order or zero Job Cards
	if len(stages) == 0:
		frappe.throw(_("No stages found for Request Sheet {0}. Please create stages before creating a Work Order or Job Card.").format(request_sheet))

	return stages

# function to get all job cards related to current work order, and maximum number of job cards possible for current work order
@frappe.whitelist()
def query_dashboard_info(doc):
	doc = json.loads(doc)
	job_card = frappe.get_list('Job Card LGM', fields="*", filters={'request_sheet_link': doc["request_sheet_link"]})

	weights = doc["weighing_table_lgm"]
	max = 0
	for row in weights:
		if int(row["mixer_no"]) > max:
			max = int(row["mixer_no"]) 

	all_stages = frappe.get_list("Stages LGM", fields="name", filters={'request_sheet_link': doc["request_sheet_link"]})
	no_of_card = len(all_stages) * max
	return [no_of_card] + [job_card]