import sys
import os
from sherpa.utils.basics import Properties
from sherpa.utils.basics import Logger
from sherpa.janssen.janssen_lib import ConfigAPIClient


def main():
    properties = Properties("./local.properties", "./default.properties")
    logger = Logger(os.path.basename(__file__), local_properties.get("idp_deployment_log_level"), local_properties.get("idp_deployment_log_file"))
    run(logger, properties)


def run(logger, properties):
    file_name = os.path.basename(__file__)
    logger.debug("Starting {} deployment".format(file_name))
    config_api_client = ConfigAPIClient(logger, properties)

    config_api_client.import_attributes("./customization/attributes")
    config_api_client.patch_attributes("./customization/attributes/patch")

    config_api_client.import_scripts("./customization/script-objects")
    config_api_client.patch_scripts("./customization/script-objects/patch")

    config_api_client.import_scopes("./customization/scopes")

    config_api_client.import_clients("./customization/clients")

    config_api_client.patch_jans_auth_server_config("./customization/jans_auth_server")


if __name__ == "__main__":
    sys.exit(main())
