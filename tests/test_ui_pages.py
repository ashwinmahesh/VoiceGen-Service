"""All browser pages use the generator shell without duplicating IDs or controls."""
from html.parser import HTMLParser
import re
import unittest
from fastapi.testclient import TestClient
import server


class Page(HTMLParser):
    def __init__(self,markup):
        super().__init__();self.ids=[];self.scripts=[];self.task_tabs=[];self.catalog_links=[];self.collection_tabs=[];self.home_links=[];self.feed(markup)
    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs)
        if 'id' in attrs:self.ids.append(attrs['id'])
        if tag=='script' and 'src' in attrs:self.scripts.append(attrs['src'].split('?',1)[0])
        if attrs.get('data-mode'):self.task_tabs.append(attrs)
        if attrs.get('id')=='browse-voices':self.catalog_links.append((tag,attrs))
        if attrs.get('data-collection'):self.collection_tabs.append(attrs)
        if attrs.get('id')=='brand-home':self.home_links.append((tag,attrs))


class SharedPageTests(unittest.TestCase):
    def test_collection_defaults_to_voice_library_and_has_a_confirmation_dialog(self):
        page=Page(TestClient(server.app).get('/collection').text)
        self.assertEqual([tab['data-collection'] for tab in page.collection_tabs],['voices','history'])
        self.assertEqual([tab['aria-selected'] for tab in page.collection_tabs],['true','false'])
        for identity in ('show-deleted','collection-delete-dialog','collection-delete-confirm','collection-delete-cancel'):
            self.assertIn(identity,page.ids)
    def test_studio_has_two_tasks_with_a_record_source_picker_and_new_tab_catalog(self):
        markup=TestClient(server.app).get('/').text;page=Page(markup)
        self.assertEqual([tab['data-mode'] for tab in page.task_tabs],['design','record'])
        for tab in page.task_tabs:
            self.assertIn(tab['aria-controls'],page.ids)
        self.assertIn('record-source',page.ids)
        for identity in ('saved-panel','upload-panel','preset-panel','structured-delivery-subtitle'):
            self.assertIn(identity,page.ids)
        self.assertEqual(len(page.catalog_links),1)
        tag,attrs=page.catalog_links[0]
        self.assertEqual(tag,'a');self.assertEqual(attrs['target'],'_blank')
        self.assertEqual(attrs['href'],'/collection?tab=voices');self.assertIn('noopener',attrs['rel'])
        switch=re.search(r'<input [^>]*id="generate-immediately"[^>]*>',markup).group()
        self.assertIn('role="switch"',switch)
        self.assertIn('checked',switch)
        for identity in ('generate-label','generate-icon','immediate-hint'):self.assertIn(identity,page.ids)

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
            self.assertLess(page.scripts.index('/assets/recording-session.js'),page.scripts.index('/assets/app.js'))
            self.assertEqual(len(page.home_links),1,path)
            tag,attrs=page.home_links[0]
            self.assertEqual((tag,attrs['href']),('a','/'),path)
            brand=re.search(r'<a [^>]*id="brand-home".*?</a>',markup,re.S).group()
            for element in ('<img','<strong','<small'):self.assertIn(element,brand,path)

    def test_recording_recovery_asset_and_resource_navigation_are_available(self):
        client=TestClient(server.app)
        response=client.get('/assets/recording-session.js')
        self.assertEqual(response.status_code,200)
        self.assertIn('text/javascript',response.headers['content-type'])
        studio=Page(client.get('/').text)
        for identity in ('recording-connection','result-resources'):
            self.assertIn(identity,studio.ids)
        self.assertIn('job-recording',Page(client.get('/observability').text).ids)

    def test_return_strip_and_scripts_share_the_current_ui_revision(self):
        client=TestClient(server.app)
        for path in ('/', '/collection', '/observability'):
            response=client.get(path);page=Page(response.text)
            for identity in ('recording-return','recording-return-heading','recording-return-link','listening-room','working-description','recording-reconnect'):
                self.assertIn(identity,page.ids)
            versions=re.findall(r'/assets/(?:app|recording-session|observability)\.js\?v=([a-f0-9]+)',response.text)
            self.assertGreaterEqual(len(versions),2)
            self.assertEqual(len(set(versions)),1)
            self.assertEqual(response.headers['cache-control'],'no-cache')
        self.assertEqual(client.get('/assets/app.js').headers['cache-control'],'no-cache')

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
