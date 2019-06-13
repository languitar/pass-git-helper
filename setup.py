from setuptools import setup

setup(
    name='pass-git-helper',
    version='1.2.0.dev0',

    install_requires=['pyxdg'],

    extras_require={
        'test': [
            'pytest',
            'pytest-coverage',
            'pytest-mock',
        ]
    },

    py_modules=['passgithelper'],
    entry_points={
        'console_scripts': [
            'pass-git-helper = passgithelper:main',
        ]
    },


    author='Johannes Wienke',
    author_email='languitar@semipol.de',
    url='https://github.com/languitar/pass-git-helper',
    description='A git credential helper interfacing with pass, '
                'the standard unix password manager.',

    license='LGPLv3+',
    keywords=['git', 'passwords', 'pass', 'credentials', 'password store'],
    classifiers=[
        'Programming Language :: Python :: 3',
        'Topic :: Utilities',
        'License :: OSI Approved :: '
        'GNU Lesser General Public License v3 or later (LGPLv3+)'
    ])
