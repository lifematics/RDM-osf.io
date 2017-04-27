import logging
import time

from website.app import init_app
from website.identifiers.utils import get_top_level_domain, request_identifiers_from_ezid

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


def add_identifiers_to_preprints():
    from osf.models import PreprintService

    preprints_without_identifiers = PreprintService.objects.filter(identifiers__isnull=True)
    logger.info('About to add identifiers to {} preprints.'.format(preprints_without_identifiers.count()))

    for preprint in preprints_without_identifiers:
        logger.info('Saving identifier for preprint {} from source {}'.format(preprint._id, preprint.provider.name))

        ezid_response = request_identifiers_from_ezid(preprint)
        preprint.set_preprint_identifiers(ezid_response)
        preprint.save()

        doi = preprint.get_identifier('doi')
        subdomain = get_top_level_domain(preprint.provider.external_url)
        assert subdomain.upper() in doi.value
        assert preprint._id.upper() in doi.value

        logger.info('Created DOI {} for Preprint from service {}'.format(doi.value, preprint.provider.name))
        time.sleep(1)

    logger.info('Finished Adding identifiers to {} preprints.'.format(preprints_without_identifiers.count()))


if __name__ == '__main__':
    init_app(routes=False)
    add_identifiers_to_preprints()
