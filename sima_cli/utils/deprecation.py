from datetime import date


NEAT_GA_DATE = date(2026, 6, 20)


def should_show_post_neat_ga_deprecation_notice() -> bool:
    return date.today() > NEAT_GA_DATE
