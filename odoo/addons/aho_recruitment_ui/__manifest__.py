{
    'name': 'AI Hiring Operator - Recruitment UI',
    'version': '1.0',
    'description': 'Light recruitment navigation, applicant tracking, stage counts and explicit rule-based warnings.',
    'category': 'Human Resources/Recruitment',
    'summary': 'Light recruitment workspace, collapsible navigation, cards and candidate history',
    'depends': ['hr_recruitment', 'rag_interview_agent', 'rag_filter_agent'],
    'data': ['views/applicant.xml', 'views/interview.xml', 'views/templates.xml', 'views/recruitment.xml'],
    'assets': {
        'web.assets_backend': [
            'aho_recruitment_ui/static/src/js/material_sidebar.js',
            'aho_recruitment_ui/static/src/xml/material_sidebar.xml',
            'aho_recruitment_ui/static/src/js/stage_funnel.js',
            'aho_recruitment_ui/static/src/xml/stage_funnel.xml',
            'aho_recruitment_ui/static/src/css/design_system.css',
            'aho_recruitment_ui/static/src/css/material_sidebar.scss',
            'aho_recruitment_ui/static/src/css/workspace_components.scss',
        ],
    },
    'author': 'T10',
    'license': 'LGPL-3',
    'installable': True,
}
