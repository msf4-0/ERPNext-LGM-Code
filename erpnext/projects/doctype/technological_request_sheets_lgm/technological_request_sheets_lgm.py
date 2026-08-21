# -*- coding: utf-8 -*-
# Copyright (c) 2023, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

from __future__ import unicode_literals
import frappe, json
from frappe.model.document import Document
from frappe import _

class TechnologicalRequestSheetsLGM(Document):
	def on_submit(self):
		create_item_from_reference_no(self)

def create_item_from_reference_no(doc):
	ITEM_GROUP_NAME = "Products"

	item_code = doc.reference_no

	if frappe.db.exists("Item", item_code):
		return

	_ensure_item_group_exists(ITEM_GROUP_NAME)

	item = frappe.get_doc({
		"doctype": "Item",
		"item_code": item_code,
		"item_name": item_code,
		"item_group": ITEM_GROUP_NAME,
		"stock_uom": "Gram",
		"is_stock_item": 1,
	})
	item.insert(ignore_permissions = True)

def _ensure_item_group_exists(item_group_name):
	"""
	If the item group doesn't exist, create it
	"""
	if frappe.db.exists("Item Group", item_group_name):
		return

	frappe.get_doc({
		"doctype": "Item Group",
		"item_group_name": item_group_name,
		"parent_item_group": "All Item Groups",
		"is_group": 0,
	}).insert(ignore_permissions = True)

"""Calculates the total waste of ingredients before production"""
@frappe.whitelist()
def calculate_waste(doc):
	doc = json.loads(doc)

	if not (doc.get("compounding_ingredients") and doc.get("curing_ingredients")):
		return 0
		
	mb = doc["compounding_ingredients"][len(doc["compounding_ingredients"]) -1]
	curing = doc["curing_ingredients"][0]

	mb_mixer_count = int(mb["select_mixer_no"])
	mb_waste = 0

	for i in range(1, mb_mixer_count+1):
		waste_name = "mixer_" + str(i)
		mixer_name = "mixer_" + str(i)
		mb_waste += float(mb[waste_name]) - float(curing[mixer_name])

	mb_waste = round(mb_waste, 2)
	return mb_waste


@frappe.whitelist()
def create_work_order_lgm(doc):
	doc = json.loads(doc)

	# Prevent duplicate work orders
	if len(frappe.db.get_all('Work Order LGM', fields="name", filters={"request_sheet_link": doc["name"]})) > 0:
		frappe.throw(_("Work Order for current technological request sheet already exists."))
	
	ingredient_list = []
	compounding_list = doc.get("compounding_ingredients", [])
	curing_list = doc.get("curing_ingredients", [])
	
	# Map the lists to their respective ingredient types
	type_list_pairs = [("Compounding", compounding_list), ("Curing", curing_list)]

	for ingredient_type, type_list in type_list_pairs:
		for list_object in type_list:
			mixer_no = int(list_object.get("select_mixer_no", 0))
			ingredient_name = list_object.get("ingredient")

			if ingredient_name != "Masterbatch":
				for i in range(1, mixer_no + 1):
					# Fetch weight dynamically based on mixer number
					weight = list_object.get(f"mixer_{i}")
					if weight is not None:
						ingredient_list.append({
							"ingredient_type": ingredient_type,
							"ingredient": ingredient_name,
							"ingredient_weight": weight,
							"mixer_no": i
						})

	# Generate and insert the Work Order document
	work_order_lgm = frappe.get_doc(dict(
		doctype='Work Order LGM',
		request_sheet_link=doc["name"],
		weighing_table_lgm=ingredient_list,
	)).insert()

	work_order_lgm.save()

	# Return the name of the newly generated document to the frontend
	return work_order_lgm.name