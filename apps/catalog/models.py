from django.db import models
from django.db.models import Q

# ---------- 카테고리 / 태그 ----------
class MenuCategory(models.Model):
    category_id = models.BigAutoField(primary_key=True)
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="children")
    name = models.TextField()
    slug = models.SlugField(max_length=120, unique=True, null=True, blank=True)
    rank = models.IntegerField(default=1000)
    active = models.BooleanField(default=True)

    class Meta:
        db_table = "menu_category"
        ordering = ("rank", "category_id")

    def __str__(self): return self.name

class ItemTag(models.Model):
    tag_id = models.BigAutoField(primary_key=True)
    name = models.CharField(max_length=100, unique=True)

    class Meta:
        db_table = "item_tag"
        ordering = ("name",)

    def __str__(self): return self.name

# ---------- 메뉴 아이템 ----------
class MenuItem(models.Model):
    item_id = models.BigAutoField(primary_key=True)
    code = models.CharField(max_length=120, unique=True)
    name = models.TextField()
    description = models.TextField(null=True, blank=True)
    category = models.ForeignKey(MenuCategory, null=True, blank=True, on_delete=models.SET_NULL)
    unit = models.CharField(max_length=50, null=True, blank=True)  # "인분", "병", "잔" 등
    base_price_cents = models.PositiveIntegerField()
    active = models.BooleanField(default=True)
    attrs = models.JSONField(default=dict, blank=True)  # 알러지/원산지 등

    tags = models.ManyToManyField(ItemTag, through="ItemTagMap", blank=True)

    class Meta:
        db_table = "menu_item"
        indexes = [models.Index(fields=["name"], name="idx_menu_item_name")]

    def __str__(self): return self.name

class ItemTagMap(models.Model):
    item = models.ForeignKey(MenuItem, on_delete=models.CASCADE)
    tag = models.ForeignKey(ItemTag, on_delete=models.CASCADE)

    class Meta:
        db_table = "item_tag_map"
        unique_together = (("item", "tag"),)

# ---------- 아이템 옵션(그룹/옵션) ----------
class OptionSelectMode(models.TextChoices):
    SINGLE = "single", "Single"
    MULTI  = "multi", "Multi"

class PriceMode(models.TextChoices):
    ADDON       = "addon", "Addon"
    MULTIPLIER  = "multiplier", "Multiplier"

class ItemOptionGroup(models.Model):
    group_id = models.BigAutoField(primary_key=True)
    item = models.ForeignKey(MenuItem, on_delete=models.CASCADE, related_name="option_groups")
    name = models.TextField()
    select_mode = models.CharField(max_length=10, choices=OptionSelectMode.choices)
    min_select = models.PositiveIntegerField(default=0)
    max_select = models.IntegerField(null=True, blank=True)  # NULL = 제한 없음
    is_required = models.BooleanField(default=False)
    is_variant = models.BooleanField(default=False)          # true면 사이즈/용량 같은 "실변형"
    price_mode = models.CharField(max_length=12, choices=PriceMode.choices, default=PriceMode.ADDON)
    rank = models.IntegerField(default=1000)

    class Meta:
        db_table = "item_option_group"
        ordering = ("rank", "group_id")
        constraints = [
            models.CheckConstraint(
                name="ck_item_optgrp_max_nonneg",
                check=Q(max_select__isnull=True) | Q(max_select__gte=0),
            ),
        ]

    def __str__(self): return f"{self.item.name} / {self.name}"

class ItemOption(models.Model):
    option_id = models.BigAutoField(primary_key=True)
    group = models.ForeignKey(ItemOptionGroup, on_delete=models.CASCADE, related_name="options")
    name = models.TextField()
    price_delta_cents = models.PositiveIntegerField(default=0)   # price_mode=addon일 때 사용
    multiplier = models.DecimalField(max_digits=7, decimal_places=3, null=True, blank=True)  # price_mode=multiplier
    is_default = models.BooleanField(default=False)
    rank = models.IntegerField(default=1000)

    class Meta:
        db_table = "item_option"
        ordering = ("rank", "option_id")

    def __str__(self): return self.name

# ---------- 서빙 스타일 ----------
class ServingStyle(models.Model):
    style_id = models.BigAutoField(primary_key=True)
    code = models.CharField(max_length=60, unique=True)       # 'simple' / 'grand' / 'deluxe'
    name = models.TextField()
    price_mode = models.CharField(max_length=12, choices=PriceMode.choices)
    price_value = models.DecimalField(max_digits=7, decimal_places=2)  # 배수 또는 가산금액
    notes = models.TextField(null=True, blank=True)

    class Meta:
        db_table = "serving_style"

    def __str__(self): return self.name

# ---------- 디너(코스) ----------
class DinnerType(models.Model):
    dinner_type_id = models.BigAutoField(primary_key=True)
    code = models.CharField(max_length=120, unique=True)
    name = models.TextField()
    description = models.TextField(null=True, blank=True)
    base_price_cents = models.PositiveIntegerField()
    active = models.BooleanField(default=True)

    class Meta:
        db_table = "dinner_type"

    def __str__(self): return self.name

class DinnerTypeDefaultItem(models.Model):
    dinner_type = models.ForeignKey(DinnerType, on_delete=models.CASCADE)
    item = models.ForeignKey(MenuItem, on_delete=models.RESTRICT)
    default_qty = models.DecimalField(max_digits=10, decimal_places=2)  # 기본 수량
    included_in_base = models.BooleanField(default=True)
    notes = models.TextField(null=True, blank=True)

    class Meta:
        db_table = "dinner_type_default_item"
        unique_together = (("dinner_type", "item"),)

class DinnerStyleAllowed(models.Model):
    dinner_type = models.ForeignKey(DinnerType, on_delete=models.CASCADE)
    style = models.ForeignKey(ServingStyle, on_delete=models.CASCADE)

    class Meta:
        db_table = "dinner_style_allowed"
        unique_together = (("dinner_type", "style"),)

# (선택) 디너 옵션(코스 레벨)
class DinnerOptionGroup(models.Model):
    group_id = models.BigAutoField(primary_key=True)
    dinner_type = models.ForeignKey(DinnerType, on_delete=models.CASCADE, related_name="option_groups")
    name = models.TextField()
    select_mode = models.CharField(max_length=10, choices=OptionSelectMode.choices)
    min_select = models.PositiveIntegerField(default=0)
    max_select = models.IntegerField(null=True, blank=True)
    is_required = models.BooleanField(default=False)
    price_mode = models.CharField(max_length=12, choices=PriceMode.choices, default=PriceMode.ADDON)
    rank = models.IntegerField(default=1000)

    class Meta:
        db_table = "dinner_option_group"
        ordering = ("rank", "group_id")

class DinnerOption(models.Model):
    option_id = models.BigAutoField(primary_key=True)
    group = models.ForeignKey(DinnerOptionGroup, on_delete=models.CASCADE, related_name="options")
    item = models.ForeignKey(MenuItem, null=True, blank=True, on_delete=models.RESTRICT)  # 아이템 선택형
    name = models.TextField(null=True, blank=True)  # 아이템 없이 이름만 있는 선택지
    price_delta_cents = models.PositiveIntegerField(default=0)
    multiplier = models.DecimalField(max_digits=7, decimal_places=3, null=True, blank=True)
    is_default = models.BooleanField(default=False)
    rank = models.IntegerField(default=1000)

    class Meta:
        db_table = "dinner_option"
        constraints = [
            models.CheckConstraint(
                name="ck_dinner_opt_has_name_or_item",
                check=Q(item__isnull=False) | Q(name__isnull=False),
            ),
        ]

# (선택) 아이템 판매 가능 시간/기간
class ItemAvailability(models.Model):
    item = models.ForeignKey(MenuItem, on_delete=models.CASCADE)
    dow = models.IntegerField()  # 0=일 … 6=토
    start_time = models.TimeField()
    end_time = models.TimeField()
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)

    class Meta:
        db_table = "item_availability"
        constraints = [
            models.CheckConstraint(name="ck_item_avail_dow", check=Q(dow__gte=0) & Q(dow__lte=6)),
        ]
        unique_together = (("item", "dow", "start_time"),)
