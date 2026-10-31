{
    'name': 'RAG Filter Agent',
    'version': '1.0',
    'category': 'Human Resources/Recruitment',
    'summary': 'Rank CV PDFs against a JD PDF with OpenAI: match %, matched and missing requirements',
    'depends': ['hr_recruitment'],
    'data': [
        'security/ir.access.csv',
        'views/views.xml',
        'views/settings.xml',
    ],
    'author': 'T10',
    'license': 'LGPL-3',
}
