/** @odoo-module **/

import { Component, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { standardFieldProps } from "@web/views/fields/standard_field_props";

const LIMIT = 50;
const BAR_HEIGHT = 150;
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "Mei", "Jun", "Jul", "Agu", "Sep", "Okt", "Nov", "Des"];
const NUMERIC = ["num", "int", "pct", "money"];
const decimal = new Intl.NumberFormat("id-ID", { maximumFractionDigits: 1 });
const integer = new Intl.NumberFormat("id-ID", { maximumFractionDigits: 0 });
const sortValue = (cell) => (cell && typeof cell === "object" ? cell.text : cell);

export class SalesDeliveryResult extends Component {
    static template = "mst17_sales_delivery.SalesDeliveryResult";
    static props = { ...standardFieldProps };

    setup() {
        this.state = useState({ tab: "salesperson", sort: {}, all: {} });
    }

    get data() {
        return this.props.record.data[this.props.name];
    }

    get activeTab() {
        return this.data.tabs.find((tab) => tab.key === this.state.tab) || this.data.tabs[0];
    }

    get rows() {
        const tab = this.activeTab;
        const sort = this.state.sort[tab.key];
        let rows = tab.rows;
        if (sort) {
            const sign = sort.asc ? 1 : -1;
            rows = [...rows].sort((a, b) => {
                const [x, y] = [sortValue(a[sort.col]), sortValue(b[sort.col])];
                return (x > y ? 1 : x < y ? -1 : 0) * sign;
            });
        }
        return this.state.all[tab.key] ? rows : rows.slice(0, LIMIT);
    }

    get chartMax() {
        return Math.max(1, ...this.data.chart.map((month) => month.sent));
    }

    barHeight(value) {
        return value ? Math.max(2, Math.round((value / this.chartMax) * BAR_HEIGHT)) : 0;
    }

    isNumeric(column) {
        return NUMERIC.includes(column.type);
    }

    sortIcon(col) {
        const sort = this.state.sort[this.activeTab.key];
        return sort && sort.col === col ? (sort.asc ? "fa-sort-asc" : "fa-sort-desc") : "";
    }

    sortBy(col) {
        const key = this.activeTab.key;
        const sort = this.state.sort[key];
        // Numbers start from the largest, text from A.
        const asc = sort && sort.col === col ? !sort.asc : !this.isNumeric(this.activeTab.columns[col]);
        this.state.sort[key] = { col, asc };
    }

    showAll() {
        this.state.all[this.activeTab.key] = true;
    }

    format(cell, column) {
        if (cell === "" || cell === null || cell === undefined) {
            return "";
        }
        if (typeof cell === "object") {
            return cell.text;
        }
        switch (column.type) {
            case "num":
                return decimal.format(cell);
            case "int":
                return integer.format(cell);
            case "pct":
                return `${decimal.format(cell)}%`;
            case "money":
                return `${cell < 0 ? "− " : ""}Rp ${integer.format(Math.abs(cell))}`;
            case "date": {
                const [year, month, day] = cell.split("-");
                return `${Number(day)} ${MONTHS[month - 1]} ${year}`;
            }
            default:
                return cell;
        }
    }
}

registry.category("fields").add("sales_delivery_result", {
    component: SalesDeliveryResult,
    supportedTypes: ["json"],
});
