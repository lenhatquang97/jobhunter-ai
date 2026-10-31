{
    'name': 'Jobhunter Chat', 'version': '20.0.1.0.0', 'license': 'LGPL-3',
    'description': 'Native recruiting chat with private history, read-only evidence tools, source citations, actual agent activity, and recruiter-confirmed job proposals.',
    'depends': ['web', 'hr_recruitment', 'calendar'],
    'data': ['security/ir.access.csv', 'views/chat_views.xml', 'views/workflow_templates.xml'],
    'assets': {'web.assets_backend': ['t10_jobhunter_chat/static/src/**/*'],
               'web.assets_unit_tests': ['t10_jobhunter_chat/static/tests/**/*']},
    'installable': True, 'application': False,
}
