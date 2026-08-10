from django.contrib import admin
from django.urls import path
from django.views.generic import RedirectView
from scrapers import crawl_views, views

urlpatterns = [
    # Registered before admin.site.urls so these win the /admin/... match;
    # admin_view() enforces the admin login on each of them.
    path("admin/scrapers/", admin.site.admin_view(views.index), name="scrapers_index"),
    path("admin/scrapers/fetch-due", admin.site.admin_view(views.fetch_due), name="fetch_due"),
    path("admin/scrapers/<str:name>/fetch", admin.site.admin_view(views.start_fetch), name="start_fetch"),
    path("admin/crawls/", admin.site.admin_view(crawl_views.crawls_index), name="crawls_index"),
    path("admin/crawls/new/", admin.site.admin_view(crawl_views.crawl_form), name="crawl_new"),
    path(
        "admin/crawls/<str:name>/edit/",
        admin.site.admin_view(crawl_views.crawl_form),
        name="crawl_edit",
    ),
    path(
        "admin/crawls/<str:name>/delete",
        admin.site.admin_view(crawl_views.delete_crawl),
        name="crawl_delete",
    ),
    path(
        "admin/crawls/<str:name>/run",
        admin.site.admin_view(crawl_views.run_crawl_pipeline),
        name="run_crawl_pipeline",
    ),
    path(
        "admin/crawls/<str:name>/crawl",
        admin.site.admin_view(crawl_views.start_crawl),
        name="start_crawl",
    ),
    path(
        "admin/crawls/<str:name>/extract",
        admin.site.admin_view(crawl_views.start_extract),
        name="start_extract",
    ),
    path(
        "admin/crawls/<str:name>/load",
        admin.site.admin_view(crawl_views.start_load),
        name="load_crawl",
    ),
    path(
        "admin/crawls/<str:name>/<str:snapshot_id>/abandon",
        admin.site.admin_view(crawl_views.abandon_crawl),
        name="abandon_crawl",
    ),
    path("admin/load/", admin.site.admin_view(views.load_index), name="load_index"),
    path("admin/promote/", admin.site.admin_view(views.promote_page), name="promote"),
    path("admin/promote/start", admin.site.admin_view(views.start_promote), name="start_promote"),
    path(
        "admin/promote/neo4j",
        admin.site.admin_view(views.start_neo4j_promote),
        name="start_neo4j_promote",
    ),
    path(
        "admin/load/<str:source>/<str:snapshot_id>/process",
        admin.site.admin_view(views.process_snapshot),
        name="process_snapshot",
    ),
    path("admin/data/", admin.site.admin_view(views.data_overview), name="data_overview"),
    path("admin/data/entities/", admin.site.admin_view(views.entities), name="entities"),
    path(
        "admin/data/entities/<uuid:entity_id>/",
        admin.site.admin_view(views.entity_detail),
        name="entity_detail",
    ),
    # after the uuid route so non-uuid segments ("person", "place") match as kinds
    path(
        "admin/data/entities/<str:kind>/",
        admin.site.admin_view(views.entities),
        name="entities_by_kind",
    ),
    path("admin/data/works/", admin.site.admin_view(views.works), name="works"),
    path("admin/data/gold/works/", admin.site.admin_view(views.gold_works), name="gold_works"),
    path("admin/data/review/", admin.site.admin_view(views.review), name="review"),
    path("admin/data/concerts/", admin.site.admin_view(views.concerts_list), name="concerts_list"),
    path(
        "admin/data/concerts/<int:concert_id>/",
        admin.site.admin_view(views.concert_detail),
        name="concert_detail",
    ),
    path("admin/data/recordings/", admin.site.admin_view(views.recordings_list), name="recordings_list"),
    path(
        "admin/data/recordings/<int:recording_id>/",
        admin.site.admin_view(views.recording_detail),
        name="recording_detail",
    ),
    path("admin/data/people/<str:role>/", admin.site.admin_view(views.people), name="people"),
    path(
        "admin/data/people/<str:role>/<uuid:person_id>/concerts",
        admin.site.admin_view(views.person_concerts),
        name="person_concerts",
    ),
    # role-less variant, linked from entity detail pages
    path(
        "admin/data/people/<uuid:person_id>/concerts",
        admin.site.admin_view(views.person_concerts),
        name="person_concerts_any",
    ),
    path(
        "admin/data/people/<str:role>/<uuid:person_id>/recordings",
        admin.site.admin_view(views.person_recordings),
        name="person_recordings",
    ),
    # role-less variant, linked from entity detail pages
    path(
        "admin/data/people/<uuid:person_id>/recordings",
        admin.site.admin_view(views.person_recordings),
        name="person_recordings_any",
    ),
    path("admin/", admin.site.urls),
    path("", RedirectView.as_view(url="/admin/scrapers/", permanent=False)),
]
