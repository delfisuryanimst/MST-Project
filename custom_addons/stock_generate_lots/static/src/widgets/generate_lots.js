/** @odoo-module */

import { _t } from "@web/core/l10n/translation";
import { x2ManyCommands } from "@web/core/orm_service";
import { Dialog } from "@web/core/dialog/dialog";
import { useService } from "@web/core/utils/hooks";
import { registry } from "@web/core/registry";
import { parseFloat as parseFloatValue } from "@web/views/fields/parsers";
import { getId } from "@web/model/relational_model/utils";
import { Component, useRef, onMounted } from "@odoo/owl";

export class GenerateLotsDialog extends Component {
    static template = "stock_generate_lots.GenerateLotsDialog";
    static components = { Dialog };
    static props = {
        move: { type: Object },
        close: { type: Function },
    };

    setup() {
        this.title = _t("Generate Lot numbers");
        this.orm = useService("orm");
        this.firstLot = useRef("firstLot");
        this.qtyPerLot = useRef("qtyPerLot");
        this.qtyReceived = useRef("qtyReceived");
        this.keepLines = useRef("keepLines");
        this.uomName = this.props.move.data.product_uom?.[1] || "";
        onMounted(() => {
            const demand = this.props.move.data.product_uom_qty || 0;
            this.qtyPerLot.el.value = demand;
            this.qtyReceived.el.value = demand;
        });
    }

    async _onGenerate() {
        const move = this.props.move;
        const moveLineVals = await this.orm.call(
            "stock.move",
            "action_generate_lot_line_vals_by_quantity",
            [
                {
                    ...move.context,
                    default_product_id: move.data.product_id[0],
                    default_location_dest_id: move.data.location_dest_id[0],
                    default_location_id: move.data.location_id[0],
                },
                this.firstLot.el.value,
                parseFloatValue(this.qtyPerLot.el.value || "0"),
                parseFloatValue(this.qtyReceived.el.value || "0"),
            ]
        );

        // Same mechanism as the standard "Import Serials/Lots" dialog (stock/generate_serial.js).
        const lines = move.data.move_line_ids;
        const newLines = moveLineVals.map((values) =>
            lines._createRecordDatapoint(values, {
                mode: "readonly",
                virtualId: getId("virtual"),
                manuallyAdded: false,
            })
        );
        if (!this.keepLines.el.checked) {
            await lines._applyCommands(
                lines._currentIds.map((currentId) => [x2ManyCommands.DELETE, currentId])
            );
        }
        lines.records.push(...newLines);
        lines._commands.push(
            ...newLines.map((record) => [x2ManyCommands.CREATE, record._virtualId])
        );
        lines._currentIds.push(...newLines.map((record) => record._virtualId));
        await lines._onUpdate();
        this.props.close();
    }
}

class GenerateLots extends Component {
    static template = "stock_generate_lots.GenerateLots";

    setup() {
        this.dialog = useService("dialog");
    }

    openDialog() {
        this.dialog.add(GenerateLotsDialog, { move: this.props.record });
    }
}

registry.category("view_widgets").add("generate_lots", { component: GenerateLots });
