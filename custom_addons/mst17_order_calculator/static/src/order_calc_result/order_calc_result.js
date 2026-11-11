/** @odoo-module **/

import { Component, useState } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { standardFieldProps } from "@web/views/fields/standard_field_props";

const LIMIT = 20;

export class OrderCalcResult extends Component {
    static template = "mst17_order_calculator.OrderCalcResult";
    static props = { ...standardFieldProps };

    setup() {
        this.limit = LIMIT;
        this.state = useState({
            tab: "orders",
            sortKey: "day",
            sortAsc: true,
            allOrders: false,
            allSequence: false,
        });
        this.orderColumns = [
            { key: "sales", label: _t("Sales") },
            { key: "kind", label: _t("Jenis") },
            { key: "day", label: _t("Tanggal") },
            { key: "qty_value", label: _t("Qty"), numeric: true },
        ];
    }

    get data() {
        return this.props.record.data[this.props.name];
    }

    get orders() {
        const { sortKey, sortAsc } = this.state;
        const sign = sortAsc ? 1 : -1;
        const orders = [...this.data.orders].sort(
            (a, b) => (a[sortKey] > b[sortKey] ? 1 : a[sortKey] < b[sortKey] ? -1 : 0) * sign
        );
        return this.state.allOrders ? orders : orders.slice(0, LIMIT);
    }

    get sequence() {
        const rows = this.data.sequence;
        if (this.state.allSequence || rows.length <= LIMIT) {
            return rows;
        }
        // Keep the "your order" row visible when the list is cut.
        return [...rows.slice(0, LIMIT - 1), rows[rows.length - 1]];
    }

    sortBy(key) {
        if (this.state.sortKey === key) {
            this.state.sortAsc = !this.state.sortAsc;
        } else {
            Object.assign(this.state, { sortKey: key, sortAsc: true });
        }
    }
}

registry.category("fields").add("order_calc_result", {
    component: OrderCalcResult,
    supportedTypes: ["json"],
});
