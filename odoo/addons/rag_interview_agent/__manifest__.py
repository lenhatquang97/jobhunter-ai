{
    'name': 'RAG Interview Agent',
    'version': '1.0',
    'category': 'Human Resources/Recruitment',
    'summary': 'Live voice interview: OpenAI Realtime speaks, a RAG agent over company + JD + CV decides',
    'depends': ['hr_recruitment', 'mail'],
    'data': [
        'security/ir.access.csv',
        'data/data.xml',
        'views/views.xml',
        'views/settings.xml',
        'views/templates.xml',
    ],
    'author': 'T10',
    'license': 'LGPL-3',
}
