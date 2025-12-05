from renderxboxchat.static import __template, document_head, search_component, nav_component, sort_component, javascript_main


template_translations = {
    "__DOCUMENT_HEAD__": document_head,
    "__SEARCH_COMPONENT__": search_component,
    "__NAV_COMPONENT__": nav_component,
    "__SORT_COMPONENT__": sort_component,
    "__JAVASCRIPT_MAIN__": javascript_main,
}

def replace_template_parts(template: str, translations: dict[str, str]) -> str:
    for key, value in translations.items():
        template = template.replace(key, value)
    return template

HTML_TEMPLATE = replace_template_parts(__template, template_translations)
VIDEO_TEMPLATE = """
<div class="my-2">
    <video controls class="rounded-lg border max-w-full h-auto shadow">
        <source src="{src}" />
        Your browser does not support the video tag.
    </video>
</div>
"""
IMAGE_TEMPLATE = """
<div class="my-2">
    <img src="{src}"
        alt="feed item"
        class="rounded-lg border max-w-full h-auto shadow" />
</div>
"""
VIDEO_EXTS = ["mp4", "webm", "mov", "mkv"]