# -*- coding: utf-8 -*-
# Copyright (c) 2024, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

from __future__ import unicode_literals
import frappe, json
from frappe import _
from frappe.utils import flt, time_diff_in_hours, get_datetime, time_diff, get_link_to_form
from frappe.model.mapper import get_mapped_doc
from frappe.model.document import Document
from erpnext.projects.doctype.work_order_lgm.work_order_lgm import (
    update_status as update_wo_status,
    build_ingredient_row
)

class JobCardLGM(Document):
    def validate(self):
        self.validate_time_logs()
        self.set_status()

    def validate_time_logs(self):
        self.total_completed_qty = 0.0
        self.total_time_in_mins = 0.0

        if self.get('time_logs'):
            for d in self.get('time_logs'):
                if get_datetime(d.from_time) > get_datetime(d.to_time):
                    frappe.throw(_("Row {0}: From time must be less than to time").format(d.idx))

                data = self.get_overlap_for(d)
                if data:
                    frappe.throw(_("Row {0}: From Time and To Time of {1} is overlapping with {2}")
                        .format(d.idx, self.name, data.name))

                if d.from_time and d.to_time:
                    d.time_in_mins = time_diff_in_hours(d.to_time, d.from_time) * 60
                    self.total_time_in_mins += d.time_in_mins

                if d.completed_qty:
                    self.total_completed_qty += d.completed_qty

            self.total_completed_qty = flt(self.total_completed_qty, self.precision("total_completed_qty"))

            if self.for_quantity and self.total_completed_qty > self.for_quantity:
                frappe.throw(_("Total completed qty ({0}) cannot exceed qty for manufacture ({1}). This usually means the Job Card has a duplicate or orphaned time log row.")
                 .format(frappe.bold(self.total_completed_qty), frappe.bold(self.for_quantity)))

    def get_overlap_for(self, args):
        existing = frappe.db.sql("""select jc.name as name from
            `tabJob Card Time Log` jctl, `tabJob Card` jc where jctl.parent = jc.name and
            (
                (%(from_time)s > jctl.from_time and %(from_time)s < jctl.to_time) or
                (%(to_time)s > jctl.from_time and %(to_time)s < jctl.to_time) or
                (%(from_time)s <= jctl.from_time and %(to_time)s >= jctl.to_time))
            and jctl.name!=%(name)s
            and jc.name!=%(parent)s
            and jc.docstatus < 2
            and jctl.employee = %(employee)s """,   # jctl, not jc
            {
                "from_time": args.from_time,
                "to_time": args.to_time,
                "name": args.name or "No Name",
                "parent": args.parent or "No Name",
                "employee": args.employee            # the row's own employee, not self.employee
            }, as_dict=True)

        return existing[0] if existing else None

    def get_required_items(self):
        if not self.get('work_order'):
            return

        doc = frappe.get_doc('Work Order LGM', self.get('work_order'))

        for d in doc.weighing_table_lgm:
            if self.get('ingredient_type') == d.get('ingredient_type'):
                self.append('ingredients', {
                    'ingredient': d.ingredient,
                    'required_weight': d.actual_weight,
                    'mix_no': d.mix_no
                })

    def validate_job_card(self):
        if not self.time_logs:
            frappe.throw(_("Time logs are required for {0} {1}")
                .format(frappe.bold("Job Card"), get_link_to_form("Job Card", self.name)))

        if self.for_quantity and self.total_completed_qty != self.for_quantity:
            total_completed_qty = frappe.bold(_("Total Completed Qty"))
            qty_to_manufacture = frappe.bold(_("Qty to Manufacture"))

            frappe.throw(_("The {0} ({1}) must be equal to {2} ({3})"
                .format(total_completed_qty, frappe.bold(self.total_completed_qty), qty_to_manufacture,frappe.bold(self.for_quantity))))
            
    def set_status(self, update_status=False):
        if self.status == "On Hold": return

        self.status = {
            0: "Open",
            1: "Submitted",
            2: "Cancelled"
        }[self.docstatus or 0]

        if self.time_logs:
            self.status = 'Work In Progress'

        if (self.docstatus == 1 and self.for_quantity 
              and self.total_completed_qty == self.for_quantity):
            self.status = "Completed"

        if update_status:
            self.db_set('status', self.status)

    def on_update(self):
        self.update_work_order_status()

    def on_cancel(self):
        self.update_work_order_status()

    def update_work_order_status(self):
        if self.get('work_order'):
            update_wo_status(self.work_order)

    def on_submit(self):
        self.validate_job_card()
        self.create_stock_entries_on_submit()
        self.update_work_order_status()

    def create_stock_entries_on_submit(self):
        # Prevent duplicate creation if a submitted Stock Entry already exists for THIS Job Card
        if (self._get_existing_stock_entry("Material Issue") or 
              self._get_existing_stock_entry("Material Transfer")):
            frappe.throw(_("Error: Stock entries were already created previously"))

        ingredient_list = self.get("ingredients", [])
        requested_weights = {}
        actual_weights = {}

        for row in ingredient_list:
            ingredient_name = row.ingredient

            if not row.requested_weight:
                frappe.throw(_("Requested weight for ingredient: {0}, mix: {1} not set.").format(row.get("ingredient"), row.get("mix_no")))
            
            if not row.actual_weight:
                frappe.throw(_("Actual weight for ingredient: {0}, mix: {1} not set.").format(row.get("ingredient"), row.get("mix_no")))
            
            requested_weights[ingredient_name] = requested_weights.get(ingredient_name, 0) + float(row.requested_weight)
            actual_weights[ingredient_name] = actual_weights.get(ingredient_name, 0) + float(row.actual_weight)

        if not requested_weights:
            return

        shortfalls = _get_stock_shortfalls(actual_weights)
        if shortfalls:
            frappe.throw(_("Cannot submit: Insufficient stock for one or more ingredients in WIP warehouse."))

        self._create_used_material_issue(actual_weights)
        self._create_unused_material_transfer(requested_weights, actual_weights)

    def _get_existing_stock_entry(self, stock_entry_type):
        """
        Returns existing stock entry of the chosen type if it exists.
        """
        existing_entry = frappe.db.get_value(
            "Stock Entry",
            {
                "work_order_lgm": self.work_order,
                "job_card_lgm": self.name,
                "stock_entry_type": stock_entry_type,
                "docstatus": 1
            },
            "name"
        )
        if existing_entry:
            return existing_entry

    def _create_used_material_issue(self, actual_weights):
        """
        Creates material issues to consume ingredients based on actual weight
        """
        material_issue_stock_entry_details = []
        wip = _get_wip_warehouse()

        if not actual_weights:
            return

        for ingredient_name, actual_weight in actual_weights.items():
            material_issue_stock_entry_details.append(dict(
                s_warehouse=wip,
                item_code=ingredient_name,
                qty=actual_weight
            ))

        # Include job_card_lgm when inserting the new Stock Entry
        material_issue_stock_entry = frappe.get_doc(dict(
            doctype="Stock Entry",
            stock_entry_type="Material Issue",
            work_order_lgm=self.work_order,
            job_card_lgm=self.name, # Link the Stock Entry back to this Job Card
            from_warehouse=wip,
            items=material_issue_stock_entry_details,
        )).insert()

        material_issue_stock_entry.submit()

    def _create_unused_material_transfer(self, requested_weights, actual_weights):
        """
        Creates a material transfer for unused remaining weights.
        """
        wip = _get_wip_warehouse()
        unused_wip = _get_unused_wip_warehouse()
        material_transfer_stock_entry_details = []

        for ingredient_name, requested_weight in requested_weights.items():
            unused_weight = requested_weight - actual_weights.get(ingredient_name, 0)

            if unused_weight <= 0:
                continue

            material_transfer_stock_entry_details.append(dict(
                s_warehouse=wip,
                t_warehouse=unused_wip,
                item_code=ingredient_name,
                qty=unused_weight
            ))

        if not material_transfer_stock_entry_details:
            return

        # Include job_card_lgm when inserting the new Stock Entry
        material_transfer_stock_entry = frappe.get_doc(dict(
            doctype="Stock Entry",
            stock_entry_type="Material Transfer",
            work_order_lgm=self.work_order,
            job_card_lgm=self.name,
            from_warehouse=wip,
            to_warehouse=unused_wip,
            items=material_transfer_stock_entry_details,
        )).insert()

        material_transfer_stock_entry.submit()

def _get_unused_wip_warehouse():
    warehouse_name = "Unused Work In Progress"

    warehouses = frappe.get_all("Warehouse", filters = {"name": ["like", "%" + warehouse_name + "%"]}, fields="name")
    if not warehouses:
        frappe.throw(_('No warehouse matching name {0} was found').format(warehouse_name))

    unused_wip = warehouses[0]["name"]

    return unused_wip

# Whitelisted function called by JS client-side purely for stock checking
@frappe.whitelist()
def check_stock_availability(doc):
    doc = json.loads(doc)
    ingredient_list = doc.get("ingredients", [])
    weights = {}

    for row in ingredient_list:        
        if not row.get("source_warehouse"):
            frappe.throw(_("Source warehouse for ingredient: {0}, mix: {1} not set.").format(row.get("ingredient"), row.get("mix_no")))

        if row.get("actual_weight"):
            ingredient_name = row.get("ingredient")
            weights[ingredient_name] = weights.get(ingredient_name, 0) + float(row["actual_weight"])

    if not weights:
        return True

    shortfalls = _get_stock_shortfalls(weights)
    if shortfalls:
        return shortfalls

    return True

@frappe.whitelist()
def get_ingredients(doc):
    doc = json.loads(doc)
    if not doc.get('work_order'):
        return
    
    work_order_doc = frappe.get_doc('Work Order LGM', doc['work_order'])
    if doc.get('for_quantity') == 0:
        return
    
    ingredient_list = []
    for d in work_order_doc.weighing_table_lgm:
        if doc.get('ingredient_type') and doc.get('ingredient_type') == d.get('ingredient_type'):
            ingredient_list.append(build_ingredient_row(d))
        
    return ingredient_list

@frappe.whitelist()
def get_instructions(doc):
    doc = json.loads(doc)
    if not doc['work_order']:
        return
    
    instruction_list = frappe.get_doc('Technological Request Sheets LGM', doc['request_sheet']).mixing_cycle
    output = []
    for instruction in instruction_list:
        output.append({
            "mixing_time": int(instruction.mixing_time),
            "mixing_process": instruction.mixing_process
        })
    return output

@frappe.whitelist()
def get_all_job_card():
    data = frappe.get_all("Job Card LGM", fields="work_order")
    output = []
    for forms in data:
        if forms.work_order not in output:
            output.append(forms.work_order)
    return output

# function to create the Material Transfer stock entry for a work order.
# warehouse_overrides is an optional {ingredient: fallback_warehouse} map —
# populated by the user when check_stock_availability found that ingredient's
# normal source_warehouse short, and they picked a different warehouse to
# pull from instead. Any ingredient not in the map uses its own row-level
# source_warehouse as usual.
@frappe.whitelist()
def create_material_issue(doc):
    doc = json.loads(doc)
    wip = _get_wip_warehouse()

    ingredient_list = doc.get("ingredients", [])
    weights = {}

    for row in ingredient_list:
        if row.get("actual_weight"):
            key = row.get("ingredient")
            weights[key] = weights.get(key, 0) + float(row["actual_weight"])

    if not weights:
        return True

    shortfalls = _get_stock_shortfalls(weights)
    if shortfalls:
        return shortfalls

    stock_entry_details = []
    for ingredient_name, requested_weight in weights.items():
        stock_entry_details.append(dict(
            s_warehouse = wip,
            item_code = ingredient_name,
            qty = requested_weight
        ))

    stock_entry = frappe.get_doc(dict(
        doctype = "Stock Entry",
        stock_entry_type = "Material Issue",
        work_order_lgm = doc["work_order"],
        from_warehouse = wip,
        items = stock_entry_details,
    )).insert()
    stock_entry.submit()
    return True

def _get_wip_warehouse(whouse_name = "Work In Progress"):
    warehouses = frappe.get_all("Warehouse", fields="name")
    wip = None
    for warehouse in warehouses:
        if whouse_name in warehouse["name"]:
            wip = warehouse["name"]

    if not wip:
        frappe.throw(_("ERROR: No warehouse with name {0} found.").format(whouse_name))

    return wip

def _get_stock_shortfalls(weights):
    """
    Returns a list of stocks that have shortfalls (insufficient stock).
    """
    shortfalls = []
    wip_whouse = _get_wip_warehouse()

    if not weights:
        return []

    for ingredient_name, needed_qty in weights.items():
        bin_qty = frappe.get_value(
            "Bin",
            {"item_code": ingredient_name, "warehouse": wip_whouse},
            "actual_qty"
        ) or 0

        if bin_qty < needed_qty:
            shortfalls.append({
                "ingredient": ingredient_name,
                "source_warehouse": wip_whouse,
                "needed": needed_qty,
                "available": bin_qty
            })

    return shortfalls

@frappe.whitelist(allow_guest=True)
def get_weight_from_nodered():
    NAMING_SERIES_PREFIX = "PO-JOB-"

    data = json.loads(frappe.request.data)

    job_card_no = data.get("job_card") or data.get("work") # "work" kept for legacy reasons
    ingredient_name = data["name"]
    mix_no = data["mix"]
    weight = data["weight"]

    job_card_id = str(job_card_no)
    if not job_card_id.startswith(NAMING_SERIES_PREFIX):
        job_card_id = NAMING_SERIES_PREFIX + job_card_id

    try:
        doc = frappe.get_doc("Job Card LGM", job_card_id)
    except:
        frappe.throw("Job Card cannot be retrieved based on given ID.")

    ingredient_list = doc.ingredients
    for ingredient in ingredient_list:
        if ingredient.ingredient == ingredient_name and ingredient.mix_no == mix_no:
            ingredient.actual_weight = weight

            doc.save(ignore_permissions = True)
            doc.reload()
            return "found"
        
    return "not found"