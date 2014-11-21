from tests import base
import json


def setUpModule():
    base.enabledPlugins.append('cumulus')
    base.startServer()


def tearDownModule():
    base.stopServer()


class StarclusterconfigTestCase(base.TestCase):

    def setUp(self):
        super(StarclusterconfigTestCase, self).setUp()

        users = ({
            'email': 'cumulus@email.com',
            'login': 'cumulus',
            'firstName': 'First',
            'lastName': 'Last',
            'password': 'goodpassword'
        }, {
            'email': 'regularuser@email.com',
            'login': 'regularuser',
            'firstName': 'First',
            'lastName': 'Last',
            'password': 'goodpassword'
        })
        self._cumulus, self._user = \
            [self.model('user').createUser(**user) for user in users]

        self._group = self.model('group').createGroup('cumulus', self._cumulus)

    def test_create(self):
        body = {
            'config': {},
            'name': 'test'
        }
        body = json.dumps(body)

        r = self.request('/starcluster-configs', method='POST',
                         type='application/json', body=body, user=self._user)
        self.assertStatus(r, 403)

        r = self.request('/starcluster-configs', method='POST',
                         type='application/json', body=body, user=self._cumulus)
        self.assertStatus(r, 201)

        self.assertEqual(r.json['config'], {})
        self.assertEqual(r.json['name'], 'test')

        # Load config and check access
        expected_access = {
            'users': [],
            'groups': [{
                'id': self._group['_id'],
                'level': 2
            }]
        }
        config = self.model('starclusterconfig', 'cumulus').load(
            r.json['_id'], force=True)
        self.assertEqual(config['access'], expected_access)

    def test_import(self):
        body = {
            'config': {},
            'name': 'test'
        }
        body = json.dumps(body)

        r = self.request('/starcluster-configs', method='POST',
                         type='application/json', body=body, user=self._cumulus)
        self.assertStatus(r, 201)

        self.assertEqual(r.json['config'], {})
        self.assertEqual(r.json['name'], 'test')
        config_id = r.json['_id']

        with open('plugins/cumulus/plugin_tests/fixtures/test.ini') as fp:
            ini_file = fp.read()

        r = self.request('/starcluster-configs/%s/import' % str(config_id), method='PATCH',
                         type='text/plain', body=str(ini_file), user=self._cumulus)
        self.assertStatusOk(r)
        expected_config = {u'_id': config_id, u'config': {u'permission': [{u'http': {u'to_port': u'80', u'from_port': u'80', u'ip_protocol': u'tcp'}}, {u'http8080': {u'to_port': u'8080', u'from_port': u'8080', u'ip_protocol': u'tcp'}}, {u'https': {u'to_port': u'443', u'from_port': u'443', u'ip_protocol': u'tcp'}}, {u'paraview': {u'to_port': u'11111', u'from_port': u'11111', u'ip_protocol': u'tcp'}}, {u'ssh': {u'to_port': u'22', u'from_port': u'22', u'ip_protocol': u'tcp'}}], u'global': {u'default_template': u''}, u'aws': [{u'info': {u'aws_secret_access_key': u'3z/PSglaGt1MGtGJ', u'aws_region_name': u'us-west-2', u'aws_region_host': u'ec2.us-west-2.amazonaws.com', u'aws_access_key_id': u'AKRWOVFSYTVQ2Q', u'aws_user_id': u'cjh'}}], u'cluster': [
            {u'default_cluster': {u'availability_zone': u'us-west-2a', u'master_instance_type': u't1.micro', u'node_image_id': u'ami-b2badb82', u'cluster_user': u'ubuntu', u'public_ips': u'True', u'keyname': u'cjh', u'cluster_size': u'2', u'plugins': u'requests-installer', u'node_instance_type': u't1.micro', u'permissions': u'ssh, http, paraview, http8080'}}], u'key': [{u'cjh': {u'key_location': u'/home/cjh/work/source/cumulus/cjh.pem'}}], u'plugin': [{u'requests-installer': {u'setup_class': u'starcluster.plugins.pypkginstaller.PyPkgInstaller', u'packages': u'requests, requests-toolbelt'}}]}, u'name': u'test'}
        self.assertEqual(r.json, expected_config)

    def test_get(self):
        r = self.request(
            '/starcluster-configs/546a1844ff34c70456111185', method='GET', user=self._cumulus)
        self.assertStatus(r, 404)

        config = {u'permission': [{u'http': {u'to_port': u'80', u'from_port': u'80', u'ip_protocol': u'tcp'}}, {u'http8080': {u'to_port': u'8080', u'from_port': u'8080', u'ip_protocol': u'tcp'}}, {u'https': {u'to_port': u'443', u'from_port': u'443', u'ip_protocol': u'tcp'}}, {u'paraview': {u'to_port': u'11111', u'from_port': u'11111', u'ip_protocol': u'tcp'}}, {u'ssh': {u'to_port': u'22', u'from_port': u'22', u'ip_protocol': u'tcp'}}], u'global': {u'default_template': u''}, u'aws': [{u'info': {u'aws_secret_access_key': u'3z/PSglaGt1MGtGJ', u'aws_region_name': u'us-west-2', u'aws_region_host': u'ec2.us-west-2.amazonaws.com', u'aws_access_key_id': u'AKRWOVFSYTVQ2Q', u'aws_user_id': u'cjh'}}], u'cluster': [
            {u'default_cluster': {u'availability_zone': u'us-west-2a', u'master_instance_type': u't1.micro', u'node_image_id': u'ami-b2badb82', u'cluster_user': u'ubuntu', u'public_ips': u'True', u'keyname': u'cjh', u'cluster_size': u'2', u'plugins': u'requests-installer', u'node_instance_type': u't1.micro', u'permissions': u'ssh, http, paraview, http8080'}}], u'key': [{u'cjh': {u'key_location': u'/home/cjh/work/source/cumulus/cjh.pem'}}], u'plugin': [{u'requests-installer': {u'setup_class': u'starcluster.plugins.pypkginstaller.PyPkgInstaller', u'packages': u'requests, requests-toolbelt'}}]}

        body = {
            'config': config,
            'name': 'test'
        }
        body = json.dumps(body)

        r = self.request('/starcluster-configs', method='POST',
                         type='application/json', body=body, user=self._cumulus)
        self.assertStatus(r, 201)

        self.assertEqual(r.json['config'], config)
        self.assertEqual(r.json['name'], 'test')
        config_id = r.json['_id']

        r = self.request('/starcluster-configs/%s' %
                         str(config_id), method='GET', user=self._user)
        self.assertStatus(r, 403)

        r = self.request('/starcluster-configs/%s' %
                         str(config_id), method='GET', user=self._cumulus)
        self.assertStatusOk(r)
        self.assertEqual(r.json, config)

        r = self.request('/starcluster-configs/%s' % str(config_id), method='GET',
                         params={'format': 'ini'}, isJson=False, user=self._cumulus)
        self.assertStatusOk(r)

    def test_delete(self):
        body = {
            'config': {},
            'name': 'test'
        }
        body = json.dumps(body)

        r = self.request('/starcluster-configs', method='POST',
                         type='application/json', body=body, user=self._cumulus)
        self.assertStatus(r, 201)

        self.assertEqual(r.json['config'], {})
        self.assertEqual(r.json['name'], 'test')
        config_id = r.json['_id']

        r = self.request('/starcluster-configs/%s' %
                         str(config_id), method='DELETE', user=self._user)
        self.assertStatus(r, 403)

        r = self.request('/starcluster-configs/%s' %
                         str(config_id), method='DELETE', user=self._cumulus)
        self.assertStatusOk(r)

        r = self.request('/starcluster-configs/%s' %
                         str(config_id), method='GET', user=self._cumulus)
        self.assertStatus(r, 404)