"""Explicit, transactional, create-only demo import into an existing Odoo database.

Called through the maintenance task, never by a model, cron or bootstrap.
"""

import json
import os
from collections import Counter

from demo_data import ROLES, candidate, criteria, document

NAMESPACE = "jobhunter_demo_20261031"


def seed_demo(env, *, apply=False, expected_database="jobhunter_cloud", company_id=1):
    if expected_database not in ("jobhunter_cloud", "jobhunter_e2e_url_check", "jobhunter_e2e_release") or env.cr.dbname != expected_database:
        raise ValueError("Unexpected demo database")
    company = env["res.company"].browse(company_id).exists()
    if len(company) != 1 or company != env.ref("base.main_company"):
        raise ValueError("Unexpected demo company")
    required = ("hr.skill.type", "hr.skill.level", "hr.skill", "hr.job.skill", "hr.applicant.skill")
    if any(model not in env for model in required):
        raise ValueError("Native Odoo skill models are unavailable")
    recruiters = env["res.users"].search([("login", "in", ["workflow.qa1", "workflow.qa2"]), ("company_id", "=", company.id), ("active", "=", True)])
    if len(recruiters) != 2 or any("Synthetic" not in user.name for user in recruiters):
        raise ValueError("Expected existing synthetic recruiters only")
    env = env(context={**env.context, "allowed_company_ids": [company.id], "tracking_disable": True, "mail_create_nolog": True, "mail_create_nosubscribe": True, "mail_notrack": True})
    created, preserved, records = Counter(), Counter(), {}

    def item(key, model, values, collision_domain=None):
        xmlid = env["ir.model.data"].search([("module", "=", NAMESPACE), ("name", "=", key)])
        if xmlid:
            if len(xmlid) != 1 or xmlid.model != model:
                raise ValueError("Demo external ID collision")
            record = env[model].browse(xmlid.res_id).exists()
            if not record:
                raise ValueError("An owned demo record was removed; refusing replacement")
            for field in ("company_id", "job_id", "applicant_id", "res_model", "res_id", "skill_type_id", "skill_id"):
                if field in values:
                    actual = record[field]
                    actual = actual.id if hasattr(actual, "id") else actual
                    if actual != values[field]:
                        raise ValueError("Demo record ownership/parent changed; refusing import")
            preserved[model] += 1
            records[key] = record.id
            return record.id, False
        if collision_domain and env[model].search_count(collision_domain):
            raise ValueError("Unowned demo name collision; no records changed")
        created[model] += 1
        if not apply:
            return 0, True
        record = env[model].create(values)
        env["ir.model.data"].create({"module": NAMESPACE, "name": key, "model": model, "res_id": record.id, "noupdate": True})
        records[key] = record.id
        return record.id, True

    with env.cr.savepoint():
        skill_type, _ = item("skill_type", "hr.skill.type", {"name": "[DEMO] Technical and Design Skills", "sequence": 90}, [("name", "=", "[DEMO] Technical and Design Skills")])
        levels = {}
        for name, progress in (("Basic", 33), ("Intermediate", 66), ("Advanced", 100)):
            levels[name], _ = item("level_" + name.lower(), "hr.skill.level", {"name": name, "level_progress": progress, "default_level": name == "Intermediate", "skill_type_id": skill_type})
        for role_index, role in enumerate(ROLES):
            title = "[DEMO] " + role["title"]
            description = "<p>[DEMO] Fictional role for product testing. No real vacancy or hiring decision.</p><p>" + role["work"] + "</p><p>Work with a fictional cross-functional product team. Explain personal contributions and document decisions. Location: demo remote team; language: English.</p>"
            job, _ = item("job_" + role["key"], "hr.job", {"name": title, "company_id": company.id, "description": description, "requirements": criteria(role), "no_of_recruitment": 1, "interviewer_ids": [(6, 0, recruiters.ids)]}, [("name", "=", title), ("company_id", "=", company.id)])
            skills = {}
            for index, skill in enumerate(role["skills"]):
                skills[skill], _ = item(f"skill_{role['key']}_{index}", "hr.skill", {"name": skill, "skill_type_id": skill_type, "sequence": index + role_index * 5})
                item(f"expected_{role['key']}_{index}", "hr.job.skill", {"job_id": job, "skill_type_id": skill_type, "skill_id": skills[skill], "skill_level_id": levels["Advanced" if index == 0 else "Intermediate"]})
            data, mime = document(title + "\n[DEMO] Fictional job description for testing only.\n" + role["work"] + "\nRequirements:\n" + criteria(role), "pdf")
            item("jd_" + role["key"], "ir.attachment", {"name": title + " JD.pdf", "res_model": "hr.job", "res_id": job, "company_id": company.id, "type": "binary", "mimetype": mime, "raw": data})
            for scenario in range(3):
                content = candidate(role_index, scenario)
                key = f"candidate_{role['key']}_{scenario}"
                applicant, new = item(key, "hr.applicant", {"partner_name": content["name"], "email_from": content["email"], "job_id": job, "company_id": company.id, "interviewer_ids": [(6, 0, recruiters.ids)], "applicant_notes": "<p>[DEMO] Fictional profile; skills below are synthetic self-report, not an assessment.</p>"}, [("partner_name", "=", content["name"]), ("job_id", "=", job)])
                extension = ("pdf", "docx", "txt")[scenario]
                data, mime = document(content["body"], extension)
                attachment, _ = item(key + "_cv", "ir.attachment", {"name": content["name"] + " CV." + extension, "res_model": "hr.applicant", "res_id": applicant, "company_id": company.id, "type": "binary", "mimetype": mime, "raw": data})
                if apply and new:
                    env["hr.applicant"].browse(applicant).write({"cv_attachment_ids": [(4, attachment)]})
                for index, skill in enumerate(content["skills"]):
                    item(key + f"_skill_{index}", "hr.applicant.skill", {"applicant_id": applicant, "skill_type_id": skill_type, "skill_id": skills[skill], "skill_level_id": levels["Intermediate"]})
        result = {"namespace": NAMESPACE, "mode": "apply" if apply else "dry-run", "create": dict(created), "preserved": dict(preserved), "records": records}
    return result


if "env" in globals():
    result = seed_demo(env, apply=os.environ.get("JOBHUNTER_DEMO_MODE") == "apply", expected_database=os.environ.get("JOBHUNTER_DEMO_DATABASE", "jobhunter_cloud"))  # noqa: F821
    if result["mode"] == "apply":
        env.cr.commit()  # noqa: F821
    print("DEMO_RESULT=" + json.dumps(result, sort_keys=True))
