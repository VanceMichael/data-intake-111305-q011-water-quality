import csv
import io
import json
from html.parser import HTMLParser


class SourcePage(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.inside_records = False
        self.record_text = []
        self.forms = []
        self.form = None

    def handle_starttag(self, tag, attributes):
        attributes = dict(attributes)
        if tag == "script" and attributes.get("id") == "source-records":
            self.inside_records = True
        if tag == "form":
            self.form = {"action": attributes.get("action", ""), "fields": {}}
        if tag == "input" and self.form is not None and attributes.get("name"):
            self.form["fields"][attributes["name"]] = attributes.get("value", "")

    def handle_data(self, data):
        if self.inside_records:
            self.record_text.append(data)

    def handle_endtag(self, tag):
        if tag == "script":
            self.inside_records = False
        if tag == "form" and self.form is not None:
            self.forms.append(self.form)
            self.form = None


def parse_page(body):
    page = SourcePage()
    page.feed(body.decode("utf-8"))
    return page


def records(body, content_type):
    media_type = content_type.partition(";")[0].strip().lower()
    if media_type == "application/json":
        payload = json.loads(body)
        result = payload.get("items") if isinstance(payload, dict) else payload
    elif media_type == "text/csv":
        result = list(csv.DictReader(io.StringIO(body.decode("utf-8-sig"))))
    elif media_type == "text/html":
        page = parse_page(body)
        if not page.record_text:
            raise ValueError("source records block missing")
        result = json.loads("".join(page.record_text))
    else:
        raise ValueError("unsupported source content type: " + media_type)
    if not isinstance(result, list) or any(not isinstance(item, dict) for item in result):
        raise ValueError("source records must be objects")
    return result
