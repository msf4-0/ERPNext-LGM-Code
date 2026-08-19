# -*- coding: utf-8 -*-
# Copyright (c) 2024, Frappe Technologies Pvt. Ltd. and Contributors
# See license.txt

import frappe
import unittest
import json
from erpnext.projects.doctype.work_order_lgm.work_order_lgm import create_job_card_lgm
from erpnext.projects.doctype.job_card_lgm.job_card_lgm import get_ingredients

class TestWorkOrderLGM(unittest.TestCase):
    
    def setUp(self):
        """
        Runs before each test. 
        We rely entirely on Frappe's native transaction management to keep the DB clean.
        No manual SQL DELETEs here, which prevents orphaned child table rows.
        """
        frappe.reload_doc("projects", "doctype", "ingredients_weighing_table_lgm", force=True)
        frappe.reload_doc("projects", "doctype", "job_card_lgm", force=True)
        frappe.reload_doc("projects", "doctype", "mixing_cycle_lgm", force=True)
        frappe.clear_cache()
        frappe.db.rollback()

    def tearDown(self):
        """Runs after each test to rollback the transaction and prevent cross-contamination."""
        frappe.db.rollback()

    # -------------------------------------------------------------------------
    # Master Data Setup Helpers (Fixes LinkValidationErrors)
    # -------------------------------------------------------------------------

    def ensure_workstation(self, workstation_name):
        """Ensures the workstation exists in the database to pass Link validation."""
        if not frappe.db.exists("Workstation", workstation_name):
            frappe.get_doc({
                "doctype": "Workstation",
                "workstation_name": workstation_name
            }).insert(ignore_permissions=True)

    def ensure_item(self, item_code):
        """Ensures the ingredient item exists in the database to pass Link validation."""
        if not frappe.db.exists("Item", item_code):
            frappe.get_doc({
                "doctype": "Item",
                "item_code": item_code,
                "item_group": "Raw Material"
            }).insert(ignore_permissions=True)

    # -------------------------------------------------------------------------
    # Factory Methods for Test Data Generation
    # -------------------------------------------------------------------------

    def make_request_sheet(self, workstation="Test Workstation", mixing_cycle=None):
        """
        Factory method to generate a Technological Request Sheet.
        By omitting the 'name' field, we force Frappe to use its autoname sequence,
        guaranteeing zero primary key collisions across test runs.
        """
        self.ensure_workstation(workstation)

        if mixing_cycle is None:
            mixing_cycle = [{"mixing_time": 10, "mixing_process": "Default Mix"}]

        doc = frappe.get_doc({
            "doctype": "Technological Request Sheets LGM",
            "factory_reference_no": workstation,
            "mixing_cycle": mixing_cycle
        })
        
        doc.flags.ignore_mandatory = True
        doc.flags.ignore_links = True 
        doc.insert(ignore_permissions=True)
        
        return doc

    def make_work_order(self, request_sheet_name, ingredients):
        """Factory method to generate a Work Order with specific ingredients."""
        doc = frappe.get_doc({
            "doctype": "Work Order LGM",
            "request_sheet_link": request_sheet_name,
            "weighing_table_lgm": ingredients
        })

        doc.flags.ignore_mandatory = True
        doc.flags.ignore_links = True
        doc.insert(ignore_permissions=True)
        
        return doc

    def create_mock_ingredient(self, ingredient_type="Compounding", ingredient_name="Rubber", weight=50.0, mixer_no=1):
        """Helper to structure ingredient rows for the payload while ensuring the Item exists."""
        self.ensure_item(ingredient_name)
        
        return {
            "ingredient_type": ingredient_type,
            "ingredient": ingredient_name,
            "weighed": weight,
            "mixer_no": mixer_no
        }

    # -------------------------------------------------------------------------
    # Standard Behavior Tests (TC-01 to TC-07)
    # -------------------------------------------------------------------------

    def test_tc_01_standard_creation_single_type(self):
        req_sheet = self.make_request_sheet()
        ingredients = [self.create_mock_ingredient("Compounding", "Rubber", 50.0, 1)]
        work_order = self.make_work_order(req_sheet.name, ingredients)

        result = create_job_card_lgm(frappe.as_json(work_order))
        
        self.assertTrue(result)
        job_cards = frappe.get_all("Job Card LGM", filters={"work_order": work_order.name})
        self.assertEqual(len(job_cards), 1)

    def test_tc_02_standard_creation_multiple_types(self):
        req_sheet = self.make_request_sheet()
        ingredients = [
            self.create_mock_ingredient("Compounding", "Rubber", 50.0, 1),
            self.create_mock_ingredient("Curing", "Sulfur", 5.0, 1)
        ]
        work_order = self.make_work_order(req_sheet.name, ingredients)

        result = create_job_card_lgm(frappe.as_json(work_order))
        
        self.assertTrue(result)
        job_cards = frappe.get_all("Job Card LGM", filters={"work_order": work_order.name}, fields=["ingredient_type"])
        self.assertEqual(len(job_cards), 2)
        
        types_created = [jc.ingredient_type for jc in job_cards]
        self.assertIn("Compounding", types_created)
        self.assertIn("Curing", types_created)

    def test_tc_03_duplicate_prevention(self):
        req_sheet = self.make_request_sheet()
        ingredients = [self.create_mock_ingredient("Compounding", "Rubber", 50.0, 1)]
        work_order = self.make_work_order(req_sheet.name, ingredients)

        frappe.get_doc({
            "doctype": "Job Card LGM",
            "work_order": work_order.name,
            "request_sheet": req_sheet.name,
            "workstation": "Test Workstation",
            "ingredient_type": "Compounding"
        }).insert(ignore_permissions=True, ignore_mandatory=True)
        
        with self.assertRaises(frappe.exceptions.ValidationError):
            create_job_card_lgm(frappe.as_json(work_order))

    def test_tc_04_empty_ingredient_type_list(self):
        req_sheet = self.make_request_sheet()
        work_order = self.make_work_order(req_sheet.name, [])
        
        result = create_job_card_lgm(frappe.as_json(work_order))
        
        self.assertTrue(result)
        job_cards = frappe.get_all("Job Card LGM", filters={"work_order": work_order.name})
        self.assertEqual(len(job_cards), 0)

    def test_tc_05_data_mapping_verification(self):
        req_sheet = self.make_request_sheet()
        ingredients = [self.create_mock_ingredient("Compounding", "Rubber", 50.0, 1)]
        work_order = self.make_work_order(req_sheet.name, ingredients)

        create_job_card_lgm(frappe.as_json(work_order))
        
        job_card_name = frappe.get_all("Job Card LGM", filters={"work_order": work_order.name})[0].name
        job_card = frappe.get_doc("Job Card LGM", job_card_name)
        
        self.assertEqual(job_card.workstation, req_sheet.factory_reference_no)

    def test_tc_06_static_value_verification(self):
        req_sheet = self.make_request_sheet()
        ingredients = [self.create_mock_ingredient("Compounding", "Rubber", 50.0, 1)]
        work_order = self.make_work_order(req_sheet.name, ingredients)

        create_job_card_lgm(frappe.as_json(work_order))
        
        job_card_name = frappe.get_all("Job Card LGM", filters={"work_order": work_order.name})[0].name
        job_card = frappe.get_doc("Job Card LGM", job_card_name)
        
        self.assertEqual(job_card.for_quantity, 1.0)

    def test_tc_07_child_table_population(self):
        custom_mixing_cycle = [
            {"mixing_time": 45, "mixing_process": "First Stage Mix"},
            {"mixing_time": 15, "mixing_process": "Final Curing"}
        ]
        req_sheet = self.make_request_sheet(mixing_cycle=custom_mixing_cycle)
        ingredients = [self.create_mock_ingredient("Compounding", "Rubber", 50.0, 1)]
        work_order = self.make_work_order(req_sheet.name, ingredients)

        create_job_card_lgm(frappe.as_json(work_order))
        
        job_card_name = frappe.get_all("Job Card LGM", filters={"work_order": work_order.name})[0].name
        job_card = frappe.get_doc("Job Card LGM", job_card_name)
        
        self.assertEqual(len(job_card.mixing_cycle), len(req_sheet.mixing_cycle))
        self.assertEqual(job_card.mixing_cycle[0].mixing_time, req_sheet.mixing_cycle[0].mixing_time)
        self.assertEqual(job_card.mixing_cycle[1].mixing_process, req_sheet.mixing_cycle[1].mixing_process)
        self.assertEqual(len(job_card.ingredients), len(work_order.weighing_table_lgm))

    # -------------------------------------------------------------------------
    # Flaw/Bug Tests (TC-08 to TC-11)
    # Note: These tests are expected to fail until the application code is patched.
    # -------------------------------------------------------------------------

    def test_tc_08_parent_theft_bug(self):
        """TC-08: Verifies that creating a Job Card does NOT steal mixing_cycle rows from the Request Sheet."""
        req_sheet = self.make_request_sheet()
        ingredients = [self.create_mock_ingredient("Compounding", "Rubber", 50.0, 1)]
        work_order = self.make_work_order(req_sheet.name, ingredients)

        create_job_card_lgm(frappe.as_json(work_order))
        
        # Reload the original document from the DB
        req_sheet.reload()
        
        # If this fails, frappe.get_doc().insert() has stolen the child rows!
        self.assertTrue(len(req_sheet.mixing_cycle) > 0, "The Request Sheet lost its mixing cycle rows!")

    def test_tc_09_frontend_vs_backend_keyerror(self):
        """TC-09: Verifies job_card_lgm.get_ingredients doesn't crash on standard frontend payload."""
        req_sheet = self.make_request_sheet()
        work_order = self.make_work_order(req_sheet.name, [self.create_mock_ingredient("Compounding", "Rubber", 50, 1)])
        
        # Simulate payload exactly as JS `job_card_lgm.js` sends it (missing mixer_no_job_card)
        mock_frontend_payload = json.dumps({
            "doctype": "Job Card LGM",
            "work_order": work_order.name,
            "for_quantity": 1
        })

        try:
            result = get_ingredients(mock_frontend_payload)
            self.assertIsInstance(result, list)
        except KeyError as e:
            self.fail(f"Backend API crashed due to KeyError: {e}. Fix the JS/Python mismatch!")

    def test_tc_10_duplicate_check_too_broad(self):
        """TC-10: Verifies system allows adding a 'Curing' Job Card if only a 'Compounding' one exists."""
        req_sheet = self.make_request_sheet()
        
        # 1. Start with only Compounding
        work_order = self.make_work_order(req_sheet.name, [self.create_mock_ingredient("Compounding", "Rubber", 50.0, 1)])
        create_job_card_lgm(frappe.as_json(work_order))
        
        # 2. Emulate user adding Curing to the Work Order later
        work_order.append("weighing_table_lgm", self.create_mock_ingredient("Curing", "Sulfur", 5.0, 1))
        work_order.save()
        
        try:
            # 3. Generating again should NOT throw an error, it should just add the missing Curing card
            create_job_card_lgm(frappe.as_json(work_order))
            job_cards = frappe.get_all("Job Card LGM", filters={"work_order": work_order.name})
            self.assertEqual(len(job_cards), 2)
        except frappe.exceptions.ValidationError:
            self.fail("Duplicate check is too broad! It blocked the creation of a valid, missing Job Card.")

    def test_tc_11_unhandled_null_ingredient_type(self):
        """TC-11: Verifies the system gracefully handles rows missing an ingredient_type."""
        req_sheet = self.make_request_sheet()
        ingredients = [self.create_mock_ingredient(None, "Rubber", 50.0, 1)]
        work_order = self.make_work_order(req_sheet.name, ingredients)

        with self.assertRaises(frappe.exceptions.ValidationError):
            create_job_card_lgm(frappe.as_json(work_order))