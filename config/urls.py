"""
URL configuration for config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path, include
from drf_spectacular.views import SpectacularAPIView
from django.http import HttpResponse

def scalar_docs(_):
    return HttpResponse("""
<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>API Docs</title></head>
<body>
  <div id="app"></div>
  <script src="https://cdn.jsdelivr.net/npm/@scalar/api-reference"></script>
  <script>
    Scalar.createApiReference('#app', {
      url: '/api/schema/',  // drf-spectacular에서 생성되는 스키마
      layout: 'modern',
      theme: 'purple'
      // proxyUrl: 'https://proxy.scalar.com'
    })
  </script>
</body></html>
    """)

urlpatterns = [
    path('admin/', admin.site.urls),
    path("api/auth/", include("apps.accounts.urls")),
    path("api/catalog/", include("apps.catalog.urls")), 
    path("api/orders/", include("apps.orders.urls")),
    path("api/schema/", SpectacularAPIView.as_view()),    # 스키마 JSON/YAML
    path("api/docs/", scalar_docs),

    # # 250922: 직원 페이지는 내부망으로 뺄 수도 있음. 플라스크로 하던지 프론트 해주신다고 하면 또 이어서 하면 될 듯
    # #         근데 플라스크로 내가 만드는게 편할듯
    # path("api/staff"), include("apps.staff.urls"), 
]
