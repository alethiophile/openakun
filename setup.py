#!python

from setuptools import setup

setup(
    name='openakun',
    version='0.0.1a',
    description="Open real-time questing engine",
    license='MIT',
    packages=['openakun'],
    install_requires=['Flask', 'passlib', 'fastpbkdf2', 'alembic',
                      'SQLAlchemy', 'psycopg2', 'flask-login', 'bleach',
                      'flask-socketio', 'eventlet' ],
    python_requires='~=3.6',
    entry_points={
        'console_scripts': [
            'openakun_initdb = openakun.pages:init_db',
        ]
    },
)
