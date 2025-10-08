from open_ire.spiders.gsearch import GSearchSpider


class CDCStacksSpider(GSearchSpider):
    name = "cdc_stacks"
    gsearch_url = "https://stacks.cdc.gov/gsearch"
    view_detail_path = "/view/cdc/"
