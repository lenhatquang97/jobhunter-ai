import { Component, useProps } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { standardFieldProps } from "@web/views/fields/standard_field_props";

/** Stage counts for one job position. Clicking a stage opens the applicant cards of that stage only. */
export class AhoStageFunnel extends Component {
    static template = "aho_recruitment_ui.StageFunnel";
    props = useProps({ ...standardFieldProps });

    setup() {
        this.action = useService("action");
    }

    get stages() {
        return this.props.record.data[this.props.name] || [];
    }

    get max() {
        return Math.max(1, ...this.stages.map((s) => s.count));
    }

    barWidth(stage) {
        return `${Math.round((stage.count / this.max) * 100)}%`;
    }

    openStage(stage, ev) {
        ev.stopPropagation();
        const jobId = this.props.record.resId;
        const jobName = this.props.record.data.name || "";
        this.action.doAction({
            type: "ir.actions.act_window",
            name: `${jobName} · ${stage.name}`,
            res_model: "hr.applicant",
            views: [[false, "kanban"], [false, "list"], [false, "form"]],
            domain: [["job_id", "=", jobId], ["stage_id", "=", stage.id]],
            context: { default_job_id: jobId, default_stage_id: stage.id, aho_pipeline: true,
                list_view_ref: "aho_recruitment_ui.applicant_tracking_list",
                search_view_ref: "aho_recruitment_ui.applicant_tracking_search" },
        });
    }
}

registry.category("fields").add("aho_stage_funnel", {
    component: AhoStageFunnel,
    supportedTypes: ["json"],
});

/** Rule-based warnings for one job position, most severe first. */
export class AhoWarningList extends Component {
    static template = "aho_recruitment_ui.WarningList";
    props = useProps({ ...standardFieldProps });

    get warnings() {
        const items = this.props.record.data[this.props.name] || [];
        return [...items].sort((a, b) => (a.level === "risk" ? 0 : 1) - (b.level === "risk" ? 0 : 1)).slice(0, 3);
    }

    get hidden() {
        return Math.max(0, (this.props.record.data[this.props.name] || []).length - 3);
    }
}

registry.category("fields").add("aho_warning_list", {
    component: AhoWarningList,
    supportedTypes: ["json"],
});
