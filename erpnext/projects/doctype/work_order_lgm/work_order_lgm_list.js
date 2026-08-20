// Copyright (c) 2023, Frappe Technologies Pvt. Ltd. and contributors
// For license information, please see license.txt

frappe.listview_settings['Work Order LGM'] = {
  get_indicator: function (doc) {
    var status_colors = {
      'Not Started': 'orange',
      'In Process': 'blue',
      'Completed': 'green',
    };
    var color = status_colors[doc.status] || 'grey';
    return [__(doc.status), color, 'status,=,' + doc.status];
  },
};
