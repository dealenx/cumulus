from setuptools import setup, find_packages

setup(
    name='cumulus-sftp',
    version='0.1.0',
    description='Import of files on remote host via SFTP',
    packages=find_packages(),
    install_requires=[
      'girder>=3.0.0a5',
      'cumulus-plugin'
    ],
    entry_points={
      'girder.plugin': [
          'sftp = sftp:SftpPlugin'
      ]
    }
)
