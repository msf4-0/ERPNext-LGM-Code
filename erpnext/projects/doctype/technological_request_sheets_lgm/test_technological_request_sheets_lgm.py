# -*- coding: utf-8 -*-
# Copyright (c) 2023, Frappe Technologies Pvt. Ltd. and Contributors
# See license.txt
from __future__ import unicode_literals

import frappe
import unittest

# ASSUMPTION: inferred from the import pattern used in test_work_order_lgm.py
# (erpnext.projects.doctype.work_order_lgm.work_order_lgm). Adjust if the
# actual module path for this doctype differs.
from erpnext.projects.doctype.technological_request_sheets_lgm.technological_request_sheets_lgm import (
    create_item_from_reference_no,
    _ensure_item_group_exists,
)


class TestTechnologicalRequestSheetsLGM(unittest.TestCase):

    def setUp(self):
        """Runs before each test. Relies on Frappe's transaction rollback to
        keep the DB clean, same pattern as test_work_order_lgm.py."""
        frappe.db.rollback()

    def tearDown(self):
        """Runs after each test to rollback the transaction and prevent
        cross-contamination between tests."""
        frappe.db.rollback()

    # -------------------------------------------------------------------------
    # Master Data Setup Helpers
    # -------------------------------------------------------------------------

    def ensure_workstation(self, workstation_name):
        """Ensures the workstation exists in the database to pass Link validation."""
        if not frappe.db.exists("Workstation", workstation_name):
            frappe.get_doc({
                "doctype": "Workstation",
                "workstation_name": workstation_name
            }).insert(ignore_permissions=True)

    def ensure_item_group(self, item_group_name, parent="All Item Groups"):
        """Ensures the given Item Group exists, so tests can pre-seed a
        state (e.g. 'the group already exists') independently of the
        code under test."""
        if not frappe.db.exists("Item Group", item_group_name):
            frappe.get_doc({
                "doctype": "Item Group",
                "item_group_name": item_group_name,
                "parent_item_group": parent,
                "is_group": 0,
            }).insert(ignore_permissions=True)

    def ensure_uom(self, uom_name):
        """Ensures the given UOM exists. Real ERPNext installs ship with
        'Gram' by default, but we don't rely on that - this makes the test
        self-sufficient regardless of what the test environment has."""
        if not frappe.db.exists("UOM", uom_name):
            frappe.get_doc({
                "doctype": "UOM",
                "uom_name": uom_name,
            }).insert(ignore_permissions=True)

    # -------------------------------------------------------------------------
    # Factory Methods
    # -------------------------------------------------------------------------

    def make_mock_doc(self, reference_no="RS-TEST-0001"):
        """
        A lightweight stand-in for a Technological Request Sheets LGM
        document, used for all Approach A tests.

        create_item_from_reference_no() only ever reads doc.reference_no -
        it never touches child tables, mixer fields, or anything else on the
        real doctype. So a full frappe.get_doc() with every mandatory field
        and child table populated (compounding_ingredients,
        curing_ingredients, total_weight_table, mixer_type, ...) is pure
        overhead for these tests, and per your note, satisfying V12's
        mandatory/link validation on this particular doctype is finnicky.

        frappe._dict is Frappe's own dot-accessible dict type, so
        mock_doc.reference_no works exactly like a real document attribute
        without any of the insert/validate machinery.
        """
        return frappe._dict(reference_no=reference_no)

    def make_request_sheet(self, workstation="Test Workstation", reference_no="RS-TEST-0001"):
        """
        Factory for a *real* Technological Request Sheets LGM document.
        Used only by the single Approach B test (TC-07) below, which needs
        an actual document to call .submit() on. Mirrors the
        ignore_mandatory / ignore_links pattern from
        test_work_order_lgm.py's make_request_sheet, for the same reason:
        we're not testing the doctype's own field validation here, just
        whether the on_submit hook fires.
        """
        self.ensure_workstation(workstation)

        doc = frappe.get_doc({
            "doctype": "Technological Request Sheets LGM",
            "factory_reference_no": workstation,
            "reference_no": reference_no,
        })

        doc.flags.ignore_mandatory = True
        doc.flags.ignore_links = True
        doc.insert(ignore_permissions=True)

        return doc

    # -------------------------------------------------------------------------
    # Approach A - Unit tests against create_item_from_reference_no directly
    # (no real Request Sheet document required)
    # -------------------------------------------------------------------------

    def test_tc_01_creates_item_with_expected_fields(self):
        """Baseline happy path: a fresh reference_no produces a correctly
        configured Item."""
        self.ensure_uom("Gram")
        doc = self.make_mock_doc("RS-TC01-001")

        create_item_from_reference_no(doc)

        self.assertTrue(frappe.db.exists("Item", "RS-TC01-001"))
        item = frappe.get_doc("Item", "RS-TC01-001")
        self.assertEqual(item.item_group, "Products")
        self.assertEqual(item.stock_uom, "Gram")
        self.assertEqual(item.is_stock_item, 1)

    def test_tc_02_creates_products_item_group_if_missing(self):
        """'Products' should exist after the call, regardless of whether it
        already existed beforehand.

        NOTE: this deliberately does NOT assert the group is absent before
        the call. On a shared dev/test site, "Products" may already exist
        from earlier real (non-test) submissions made through the browser -
        those commits happened outside any test transaction, so rollback
        can't undo them. Asserting a specific starting state here would be
        testing the site's history, not this function's behavior. TC-06
        already covers the "called twice, no duplicate error" case, which
        is the actual behavior worth protecting here."""
        self.ensure_uom("Gram")

        doc = self.make_mock_doc("RS-TC02-001")
        create_item_from_reference_no(doc)

        self.assertTrue(frappe.db.exists("Item Group", "Products"))

    def test_tc_03_reuses_existing_products_item_group(self):
        """Calling the function twice (two different sheets) must not try
        to re-create 'Products' and throw a DuplicateEntryError."""
        self.ensure_uom("Gram")
        self.ensure_item_group("Products")

        doc_a = self.make_mock_doc("RS-TC03-001")
        doc_b = self.make_mock_doc("RS-TC03-002")

        create_item_from_reference_no(doc_a)
        create_item_from_reference_no(doc_b)  # should not raise

        self.assertTrue(frappe.db.exists("Item", "RS-TC03-001"))
        self.assertTrue(frappe.db.exists("Item", "RS-TC03-002"))

    def test_tc_04_skips_creation_when_item_already_exists(self):
        """Per the agreed behavior: if an Item with this reference_no
        already exists, reuse it and do NOT overwrite its fields."""
        self.ensure_uom("Gram")
        self.ensure_item_group("Raw Material")
        frappe.get_doc({
            "doctype": "Item",
            "item_code": "RS-TC04-001",
            "item_group": "Raw Material",
            "stock_uom": "Nos",
        }).insert(ignore_permissions=True)

        doc = self.make_mock_doc("RS-TC04-001")
        create_item_from_reference_no(doc)

        item = frappe.get_doc("Item", "RS-TC04-001")
        # Still Raw Material / Nos - proves the existing Item was left alone,
        # not silently converted to Products / Gram.
        self.assertEqual(item.item_group, "Raw Material")
        self.assertEqual(item.stock_uom, "Nos")

    def test_tc_05_throws_on_missing_reference_no(self):
        """An empty reference_no should fail loudly and clearly, not
        produce a malformed or unnamed Item."""
        doc = self.make_mock_doc(reference_no=None)

        with self.assertRaises(frappe.exceptions.ValidationError):
            create_item_from_reference_no(doc)

    def test_tc_06_ensure_item_group_exists_is_idempotent(self):
        """Directly exercises _ensure_item_group_exists in isolation,
        independent of the Item-creation logic."""
        _ensure_item_group_exists("Products")
        _ensure_item_group_exists("Products")  # second call must be a no-op

        self.assertTrue(frappe.db.exists("Item Group", "Products"))

    # -------------------------------------------------------------------------
    # Approach B - single integration test proving the submit hook fires
    # -------------------------------------------------------------------------

    def test_tc_07_on_submit_creates_item_end_to_end(self):
        """
        The one Approach B test in this file. TC-01 through TC-06 already
        prove the Item-creation logic is correct in isolation; this test's
        only job is to confirm on_submit is actually registered on the real
        doctype and fires create_item_from_reference_no when a real document
        is submitted - i.e. that the wiring works, not just the logic.
        """
        self.ensure_uom("Gram")
        req_sheet = self.make_request_sheet(reference_no="RS-TC07-001")

        req_sheet.submit()

        self.assertTrue(frappe.db.exists("Item", "RS-TC07-001"))