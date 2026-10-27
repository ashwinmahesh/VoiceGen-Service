"""All browser pages use the generator shell without duplicating IDs or controls."""
from html.parser import HTMLParser
import re
import unittest
from fastapi.testclient import TestClient
import server


class Page(HTMLParser):
    def __init__(self,markup):
        super().__init__();self.ids=[];self.scripts=[];self.feed(markup)
    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs)
        if 'id' in attrs:self.ids.append(attrs['id'])
        if tag=='script' and 'src' in attrs:self.scripts.append(attrs['src'])


class SharedPageTests(unittest.TestCase):
    def test_every_page_has_the_generator_header_and_queue_controls(self):
        client=TestClient(server.app)
        generator=client.get('/').text
        expected=re.search(r'<header>.*?</header>',generator,re.S).group()
        for path in ('/','/collection','/collection/','/observability','/observability/'):
            response=client.get(path);self.assertEqual(response.status_code,200,path)
            markup=response.text;page=Page(markup)
            self.assertEqual(re.search(r'<header>.*?</header>',markup,re.S).group(),expected,path)
            self.assertEqual(len(page.ids),len(set(page.ids)),path)
            for identity in ('open-queue','status','unload','queue-dialog','queue-pause','queue-entries'):
                self.assertEqual(page.ids.count(identity),1,(path,identity))
            self.assertIn('/assets/app.js',page.scripts)

    def test_observability_content_and_script_only_load_on_its_page(self):
        client=TestClient(server.app)
        for path in ('/','/collection','/observability'):
            page=Page(client.get(path).text)
            observability=path=='/observability'
            self.assertEqual('observability-page' in page.ids,observability)
            self.assertEqual('collector-status' in page.ids,observability)
            self.assertEqual('/assets/observability.js' in page.scripts,observability)
            if observability:
                self.assertIn('resource-jobs',page.ids)
                self.assertIn('job-log-output',page.ids)


if __name__=='__main__':unittest.main()
