# sherpa-py-janssen is available under the MIT License. https://github.com/Identicum/sherpa-py-janssen/
# Copyright (c) 2024, Identicum - https://identicum.com/
#Gustavo J Gallardo - ggallard@identicum.com
#
# Authors:
#   Ezequiel O Sandoval - esandoval@identicum.com
#   Gustavo J Gallardo - ggallard@identicum.com
#

import json
import requests
import os
import shutil
import zipfile
import time
from sherpa.utils.clients import OIDCClient
from sherpa.utils import http
from pathlib import Path
from importlib.metadata import version


class ConfigAPIClient:

    def __init__(self, logger, properties, verify=True):
        self.logger = logger
        self.properties = properties
        self.logger.debug("ConfigAPIClient version: " + version("sherpa-py-janssen"))
        self.base_uri = 'https://{}'.format(self.properties.get('idp_hostname'))
        self.oidc_client = OIDCClient(self.base_uri, logger, verify=verify)
        self.temp_dir = './work'
        self.verify = verify
        shutil.rmtree(self.temp_dir, ignore_errors=True)
        os.mkdir(self.temp_dir, 0o744)

    def _execute_with_json_response(self, operation, endpoint, scopes, payload={}):
        self.logger.debug('{} {}', operation, endpoint)
        url = '{}{}'.format(self.base_uri, endpoint)
        self.logger.trace('Getting acc_token for operation')
        client_id = self.properties.get('configapi_client_id')
        client_secret = self.properties.get('configapi_client_secret')
        b64_creds = http.to_base64_creds(client_id, client_secret)
        is_agama_deploy = operation == 'POST' and 'agama-deployment' in endpoint
        params = {
            'grant_type': 'client_credentials',
            'scope': scopes
        }
        acc_token = self.oidc_client.request_to_token_endpoint(b64_creds, params).get('access_token')
        if is_agama_deploy:
            content_type = "application/zip"
            with open(payload, "rb") as file:
                body = file.read()
        else:
            content_type = 'application/json' if operation != 'PATCH' else 'application/json-patch+json'
            body = json.dumps(payload)

        headers = {
            'Authorization': 'Bearer {}'.format(acc_token),
            'Content-Type': content_type
        }

        self.logger.trace('OPERATION: {}, URL: {}, HEADERS: {}, DATA: {}', operation, url, headers, body)
        if operation == 'GET':
            response = requests.request(operation, url, headers=headers, verify=self.verify)
        else:
            response = requests.request(operation, url, headers=headers, data=body, verify=self.verify)
        http.validate_response(response, self.logger, 'Execute Failed - HTTP Code: {}'.format(response.status_code))
        payload = {} if operation == 'DELETE' or is_agama_deploy else response.json()
        self.logger.trace('{} JSON response - {}', operation, payload)
        return payload

    def _get_object(self, endpoint, scopes):
        return self._execute_with_json_response("GET", endpoint, scopes)
        
    def _get_files_path(self, objects_folder, extension='.json'):
        files = list()
        try:
            for directory_entry in sorted(os.scandir(objects_folder), key=lambda path: path.name):
                file_path = directory_entry.path
                if directory_entry.is_file() and file_path.endswith(extension):
                    temp_file = '{}/{}'.format(self.temp_dir,os.path.basename(file_path))
                    shutil.copyfile(file_path, temp_file)
                    self.properties.replace(temp_file)
                    files.append(temp_file)
        except:
            self.logger.debug("Folder {} is not present", objects_folder)
        return files

    def _list_folders_objs(self, path):
        return [folder for folder in Path(path).iterdir() if folder.is_dir()]

    def _load_json(self, json_file):
        json_data = json.load(json_file)
        self.logger.trace('JSON definition: {}', json_data)
        return json_data

    def _patch_objs(self, endpoint, scopes, objects_folder, inum_patch=True):
        for file_path in self._get_files_path(objects_folder):
            self.logger.debug('Processing file: {}', file_path)
            with open(file_path) as json_file:
                json_data = self._load_json(json_file)
                inum = Path(file_path).stem
                query_endpoint = '{}/{}'.format(endpoint, inum) if inum_patch else endpoint
                self._execute_with_json_response('PATCH', query_endpoint, scopes, json_data)

    def _query_by_pattern(self, endpoint, scopes, key, key_val):
        query_endpoint = '{}?pattern={}'.format(endpoint,key_val)
        query_list = self._execute_with_json_response('GET', query_endpoint, scopes)
        if not isinstance(query_list, list):
            query_list_data = query_list.get('data')
            if query_list_data is None:
                #Jans 1.1.5
                query_list_data = query_list.get('entries')
        search_result_list = [] if query_list_data is None else [ x for x in query_list_data if x.get(key) == key_val]
        return search_result_list

    def _import_obj_by_key(self, endpoint, scopes, objects_folder, key='name'):
        for file_path in self._get_files_path(objects_folder):
            self.logger.debug('Processing file: {}', file_path)
            with open(file_path) as json_file:
                json_data = self._load_json(json_file)
                key_val = json_data.get(key)
                search_result_list = self._query_by_pattern(endpoint, scopes, key, key_val)
                size_search_result_list = len(search_result_list)
                if size_search_result_list == 0:
                    self.logger.debug('POST obj {}', key_val)
                    self._execute_with_json_response('POST', endpoint, scopes, json_data)
                elif size_search_result_list == 1:
                    self.logger.debug('PUT obj {}', key_val)
                    entry = search_result_list[0]
                    entry.update(json_data)
                    self._execute_with_json_response('PUT', endpoint, scopes, entry)
                else:
                    dns_search_result_list = [x.get('inum') for x in search_result_list]
                    error_msg = 'obj with {} {} is duplicated on Jans, entries on system: {}'.format(key, key_val, dns_search_result_list)
                    self.logger.error(error_msg)
                    raise ValueError(error_msg)

    def _import_obj_by_inum(self, endpoint, scopes, objects_folder):
        for file_path in self._get_files_path(objects_folder):
            self.logger.debug('Processing file: {}', file_path)
            with open(file_path) as json_file:
                json_data = self._load_json(json_file)
                inum = json_data.get('inum')
                query_endpoint = self._build_query_endpoint(endpoint, inum)
                json_data = self._customize_for_endpoint(endpoint, objects_folder, file_path, json_data)
                current_jans_obj = {}
                try:
                    self.logger.debug('GETting object: {}', query_endpoint)
                    current_jans_obj = self._execute_with_json_response('GET', query_endpoint, scopes)
                except:
                    self.logger.debug("Object {} not present in jans", query_endpoint)
                if current_jans_obj != {}:
                    self.logger.debug('Object already exists. Starting update process.')
                    patch_operations = self._get_patch_operations(endpoint, json_data, current_jans_obj)
                    if len(patch_operations) > 0:
                        self.logger.debug('The operations patch is {}', patch_operations)
                        self._execute_with_json_response('PATCH', endpoint+"/"+inum, scopes, patch_operations)
                    else:
                        self.logger.debug('No patch operations needed.')
                else:
                    self.logger.debug('POSTing object: {} to endpoint: {}', json_data, endpoint)
                    self._execute_with_json_response('POST', endpoint, scopes, json_data)

    def _import_agama_projects(self, endpoint, scopes, objects_folder, wait_time):
        self.logger.debug("starting agama project import")
        folders_objs = self._list_folders_objs(objects_folder)
        self.logger.trace("folders: {}", folders_objs)
        self.logger.trace("Get configs and build zip files for deploy")
        agama_scripts_configs = []
        for folder in folders_objs:
            folder_name = folder.name
            folder_path = str(folder)

            project_json_file_path = "{}/{}".format(folder_path,'project.json')
            with open(project_json_file_path) as json_file:
                self.logger.trace("Extracting project_json for {}", folder_name)
                project_json_obj = self._load_json(json_file)
                self.logger.trace("config for {} is: {}", folder_name, json.dumps(project_json_obj))
                agama_scripts_configs.append(project_json_obj)
            agama_project_name = project_json_obj.get("projectName")

            self.logger.trace("building zip file for: {}", folder_name)
            zip_file_path = "{}/{}.zip".format(self.temp_dir, folder_name)
            with zipfile.ZipFile(zip_file_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for root, dirs, files in os.walk(folder_path):
                    dirs[:] = [d for d in dirs if not d.startswith('.')] # magic that removes hidden folders
                    for file in files:
                        file_path = Path(root) / file
                        zipf.write(file_path, file_path.relative_to(folder_path))
            self.logger.trace("ZIP agama file created for: {}", folder_name)

            self.logger.trace("POST agama project {}", agama_project_name)
            self._execute_with_json_response('POST', "{}/{}".format(endpoint, agama_project_name), scopes, zip_file_path)

        self.logger.trace("All agama projects posted, wait time until proceed with configs is: {} secs", wait_time)
        time.sleep(wait_time)

        self.logger.trace("Proceed with PUT configs for agama projects")
        for agama_script_config in agama_scripts_configs:
            agama_project_name = agama_script_config.get("projectName")
            configs = agama_script_config.get('configs')
            self._execute_with_json_response('PUT', "{}/configs/{}".format(endpoint, agama_project_name), scopes, configs)

        self.logger.debug("Agama projects imported successfully")

    def _get_patch_operations(self, endpoint, json_data, current_jans_obj):
        self.logger.debug('JSON from file: {}', json_data)
        self.logger.debug('Current object: {}', current_jans_obj)
        patch_operations = []

        self.logger.debug("Executing patch exceptions - https://github.com/JanssenProject/jans/issues/8370")
        if endpoint == '/jans-config-api/api/v1/openid/clients':
            self.logger.debug("Can not modify clientSecret in PATCH operation")
            json_data.pop("clientSecret", None)
            current_jans_obj.pop("clientSecret", None)

        for attributeName, attributeValue in json_data.items():
            self.logger.debug('Attr {} with Value {} Type {}', attributeName, attributeValue, type(attributeValue))
            if current_jans_obj[attributeName] != attributeValue:
                if type(attributeValue) is dict:
                    for childName, childValue in json_data["attributes"].items():
                        self.logger.debug('Attributes section. Key {} Value {}', childName, childValue)
                        op = dict(op="replace", path="/"+attributeName+"/"+childName, value=childValue)
                        patch_operations.append(op)
                else:
                    op = dict(op="replace", path="/"+attributeName, value=attributeValue)
                    patch_operations.append(op)
        return patch_operations

    def _build_query_endpoint(self, endpoint, inum):
        if endpoint == '/jans-config-api/api/v1/config/scripts':
            query_endpoint = '{}/inum/{}'.format(endpoint, inum)
        else:
            query_endpoint = '{}/{}'.format(endpoint, inum)
        return query_endpoint

    def _customize_for_endpoint(self, endpoint, objects_folder, file_path, json_data):
        if endpoint == '/jans-config-api/api/v1/config/scripts':
            self.logger.debug('loading script code into json object')
            code_file_path = '{}/{}.py'.format(objects_folder, Path(file_path).stem)
            with open(code_file_path) as code_file:
                json_data['script'] = code_file.read()
        if endpoint == '/jans-config-api/api/v1/openid/clients':
            self.logger.debug('loading scopes inum on client')
            client_scopes = json_data.get('scopes')
            if client_scopes:
                id_scopes = [x for x in client_scopes if not x.startswith("inum=")]
                #If scope id does not exist, must stop the whole operation
                for id_scope in id_scopes:
                    search_result_list = self._query_by_pattern('/jans-config-api/api/v1/scopes', 'https://jans.io/oauth/config/scopes.readonly', 'id', id_scope)
                    self.logger.trace("replacing scope id {} ", id_scope)
                    self.logger.trace("search_result_list: {}",search_result_list)
                    inum = search_result_list[0].get('dn')
                    client_scopes.append(inum)
                    client_scopes.remove(id_scope)
                    self.logger.trace("replaced with scope inum {} ", inum)
        return json_data

    def _clean_json(self, endpoint, json_obj):
        if endpoint == '/jans-config-api/api/v1/openid/clients':
            self._pop_if_not_str(json_obj, ['clientName', 'logoUri', 'clientUri', 'policyUri', 'tosUri'])

    def _pop_if_not_str(self, json_obj, attr_list):
        for key in attr_list:
            value = "" if isinstance(json_obj.get(key), str) else json_obj.pop(key, None)

############################
# Attribute operations
#
# name attr value must be included on displayName value
# Gluu searchs entries by displayName/description substring.
# If there is more than one valid value for displayName
# Always take the obj which name attr is equal to the json file value.
############################

    def import_attributes(self, objects_folder):
        self.logger.debug('Import attributes from {}', objects_folder)
        endpoint = '/jans-config-api/api/v1/attributes'
        scopes = 'https://jans.io/oauth/config/attributes.readonly https://jans.io/oauth/config/attributes.write'
        self._import_obj_by_key(endpoint, scopes, objects_folder)

############################
# scopes operations
#
# id attr value must be included on displayName value
# Gluu searchs entries by displayName/description substring.
# If there is more than one valid value for displayName
# Always take the obj which name attr is equal to the json file value.
############################

    def get_scope(self, inum):
        self.logger.debug('Getting scope {}', inum)
        endpoint = '/jans-config-api/api/v1/scopes' + inum
        scopes = 'https://jans.io/oauth/config/scopes.readonly'
        self._get_object(endpoint, scopes)

    def import_scopes(self, objects_folder):
        self.logger.debug('Import scopes from {}', objects_folder)
        endpoint = '/jans-config-api/api/v1/scopes'
        scopes = 'https://jans.io/oauth/config/scopes.write https://jans.io/oauth/config/scopes.readonly'
        self._import_obj_by_key(endpoint, scopes, objects_folder, 'id')

############################
# Client operations
#
# requires inum attr defined on the json file
# scopes can be a valid inum, or the scope id value (this value also must be defined on scope displayName definition)
############################

    def get_client(self, inum):
        self.logger.debug('Getting client {}', inum)
        endpoint = '/jans-config-api/api/v1/openid/clients/' + inum
        scopes = 'https://jans.io/oauth/config/openid/clients.readonly'
        self._get_object(endpoint, scopes)

    def import_clients(self, objects_folder):
        self.logger.debug('Import clients from {}', objects_folder)
        endpoint = '/jans-config-api/api/v1/openid/clients'
        scopes = 'https://jans.io/oauth/config/openid/clients.readonly https://jans.io/oauth/config/openid/clients.write'
        self._import_obj_by_inum(endpoint, scopes, objects_folder)

############################
# Script operations
#
# requires inum attr defined on the json file
# inside the import folder must be 2 files with same name, the json definition and the python code (.py) file per script to import
# script attr is not required on the json definition, it will be added during the import process
# patch operation only requires the json file with patch specification
############################

    def import_scripts(self, objects_folder):
        self.logger.debug('Import Script from {}', objects_folder)
        endpoint = '/jans-config-api/api/v1/config/scripts'
        scopes = 'https://jans.io/oauth/config/scripts.readonly https://jans.io/oauth/config/scripts.write'
        self._import_obj_by_inum(endpoint, scopes, objects_folder)

############################
# agama scripts operations
#
# folder structure must respect https://docs.jans.io/v1.1.5/agama/gama-format/
# project.json must have config section (can be empty) it is required to PUT step on function
# Agama projects takes 30 secs to reload average after modification
############################

    def import_agama_scripts(self, objects_folder, wait_time=30):
        self.logger.debug('Import Agama Script from {}', objects_folder)
        endpoint = '/jans-config-api/api/v1/agama-deployment'
        scopes = 'https://jans.io/oauth/config/agama.readonly https://jans.io/oauth/config/agama.write https://jans.io/oauth/config/agama.delete'
        self._import_agama_projects(endpoint, scopes, objects_folder, wait_time)

############################
# jans modules configuration
############################

    def get_auth_server_config(self):
        self.logger.debug('Getting auth-server config')
        endpoint = '/jans-config-api/api/v1/jans-auth-server/config'
        scopes = 'https://jans.io/oauth/jans-auth-server/config/properties.readonly'
        self._get_object(endpoint, scopes)


    def import_auth_server_config(self, objects_folder):
        self.logger.debug('Patch auth-server configuration from {}', objects_folder)
        endpoint = '/jans-config-api/api/v1/jans-auth-server/config'
        scopes = 'https://jans.io/oauth/jans-auth-server/config/properties.readonly https://jans.io/oauth/jans-auth-server/config/properties.write'
        self._patch_objs(endpoint, scopes, objects_folder, False)


    def get_config_api_config(self):
        self.logger.debug('Getting config-api config')
        endpoint = '/jans-config-api/api/v1/api-config'
        scopes = 'https://jans.io/oauth/config/properties.readonly'
        self._get_object(endpoint, scopes)


    def import_config_api_config(self, objects_folder):
        self.logger.debug('Patch config-api configuration from {}', objects_folder)
        endpoint = '/jans-config-api/api/v1/api-config'
        scopes = 'https://jans.io/oauth/config/properties.readonly https://jans.io/oauth/config/properties.write'
        self._patch_objs(endpoint, scopes, objects_folder, False)


    def get_scim_config(self):
        self.logger.debug('Getting scim config')
        endpoint = '/jans-config-api/scim/scim-config'
        scopes = 'https://jans.io/scim/config.readonly'
        self._get_object(endpoint, scopes)


    def import_scim_config(self, objects_folder):
        self.logger.debug('Patch scim configuration from {}', objects_folder)
        endpoint = '/jans-config-api/scim/scim-config'
        scopes = 'https://jans.io/scim/config.readonly https://jans.io/scim/config.write'
        self._patch_objs(endpoint, scopes, objects_folder, False)
