from django.core.files.storage import Storage
from django.conf import settings
from django.utils.deconstruct import deconstructible
from imagekitio import ImageKit


@deconstructible
class ImageKitStorage(Storage):
    def __init__(self):
        self.client = ImageKit(private_key=settings.IMAGEKIT_PRIVATE_KEY)

    def _save(self, name, content):
        response = self.client.files.upload(
            file=content.read(),
            file_name=name,
            folder="/angan-baari",
        )
        return response.url

    def url(self, name):
        return name

    def exists(self, name):
        return False

    def size(self, name):
        return 0