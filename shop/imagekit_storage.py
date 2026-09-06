from django.core.files.storage import Storage
from django.conf import settings
from imagekitio import ImageKit


class ImageKitStorage(Storage):
    def __init__(self):
        self.client = ImageKit(private_key=settings.IMAGEKIT_PRIVATE_KEY)

    def _save(self, name, content):
        response = self.client.files.upload(
            file=content.read(),
            file_name=name,
            folder="/angan-baari",
        )
        # response.url is the full, ready-to-use ImageKit CDN link —
        # store that directly as the "name" so url() below is trivial
        return response.url

    def url(self, name):
        return name

    def exists(self, name):
        # ImageKit auto-generates unique filenames on upload, so treat
        # every save as new — avoids local-storage name-clash checks
        return False

    def size(self, name):
        return 0  # not tracked locally; ImageKit manages this