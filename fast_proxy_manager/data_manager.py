from random import choice
from pathlib import Path
from typing import Optional, List, Union

from .file_ops import read_msgpack, write_msgpack
from .utils import ProxyDict, NoProxyAvailable, URL, ProxyIndex
from .logger import logger

from orjson import JSONDecodeError


def _validate_protocol(protocols: list[str] | str | None) -> list[str] | None:
    if protocols is None:
        return None
    if isinstance(protocols, str):
        protocols = [protocols]
    if any(protocol not in ("http", "https", "socks4", "socks5") for protocol in protocols):
        invalid_protocol = next(p for p in protocols if p not in ("http", "https", "socks4", "socks5"))
        raise ValueError(f"You can't use this protocol: {invalid_protocol}")
    return protocols


class DataManager:
    def __init__(self, msgpack: Optional[Path] = Path("proxy_data"),
                 allowed_fails_in_row: int = 2,
                 fails_without_check: int = 2,
                 percent_failed_to_remove: float = 0.5,
                 min_proxies: int = 0):
        """
        Get add and remove proxies from a list with some extra features.

        :param msgpack: Highly recommended to use it.
        Path to a store file with proxy data. If set to None, it will not store data in a file.
        :param allowed_fails_in_row: How many times a proxy can fail in a row before being removed.
        :param fails_without_check: How many times a proxy can fail before being checked for percentage of fails to remove.
        :param percent_failed_to_remove: Percentage of fails to remove a proxy.
        Example: 0.5 means 50% of tries are fails, if higher than that it gets removed.
        :param min_proxies: When len(proxies) < min_proxies -> fetch more proxies
        """

        self.allowed_fails_in_row = allowed_fails_in_row
        self.fails_without_check = fails_without_check
        self.percent_failed_to_remove = percent_failed_to_remove
        self.min_proxies = min_proxies
        self.msgpack = msgpack if msgpack else None
        self.proxies = self._load_proxies()
        logger.debug(f"Loaded {len(self.proxies)} proxies on init")
        self.last_proxy_index = None
        self.index = ProxyIndex()
        self.index.rebuild_index(self.proxies)  # Initialize index with loaded proxies

    def _load_proxies(self) -> list[dict]:
        if self.msgpack and self.msgpack.exists() and self.msgpack.stat().st_size > 0:
            try:
                return read_msgpack(self.msgpack)
            except JSONDecodeError:
                return []
        self.msgpack and self.msgpack.touch(exist_ok=True)
        return []

    def _write_data(self):
        self.msgpack and write_msgpack(self.msgpack, self.proxies)

    def _rm_duplicate_proxies(self):
        seen_urls = set()
        new_proxies = [
            proxy for proxy in reversed(self.proxies)
            if not (proxy['url'] in seen_urls or seen_urls.add(proxy['url']))
        ]
        self.proxies = new_proxies
        self.index.rebuild_index(self.proxies)  # Rebuild index after removing duplicates

    def update_data(self, remove_duplicates: bool = True):
        if remove_duplicates:
            self._rm_duplicate_proxies()
        self._write_data()

    def force_rm_last_proxy(self):
        if self.last_proxy_index is not None:
            self.rm_proxy(self.last_proxy_index)

    def feedback_proxy(self, success: bool):
        if self.last_proxy_index is None or self.last_proxy_index >= len(self.proxies):
            return

        proxy = self.proxies[self.last_proxy_index]
        if success:
            proxy["times_succeed"] = proxy.get("times_succeed", 0) + 1
            proxy["times_failed_in_row"] = 0
        else:
            proxy["times_failed"] = proxy.get("times_failed", 0) + 1
            proxy["times_failed_in_row"] = proxy.get("times_failed_in_row", 0) + 1

            total_attempts = proxy.get("times_failed", 0) + proxy.get("times_succeed", 0)
            failed_ratio = proxy.get("times_failed", 0) / total_attempts if total_attempts > 0 else 0

            should_remove = any([
                proxy.get("times_failed_in_row", 0) > self.allowed_fails_in_row,
                proxy.get("times_failed", 0) > self.fails_without_check and failed_ratio > self.percent_failed_to_remove
            ])

            if should_remove:
                logger.debug(f"Removing proxy {proxy['url']} due to "
                             f"{'too many failures in a row' if proxy.get('times_failed_in_row', 0) > self.allowed_fails_in_row else 'bad success-failure ratio'}. "
                             f"f:{proxy.get('times_failed', 0)} s:{proxy.get('times_succeed', 0)} "
                             f"f_in_row:{proxy.get('times_failed_in_row', 0)}")
            self.rm_proxy(self.last_proxy_index)
        self._write_data()

    def add_proxy(self, proxies: List[ProxyDict]):
        start_index = len(self.proxies)
        new_proxies = []
        for proxy in proxies:
            url = URL(proxy["url"])
            proxy = {
                "url": repr(url),
                "protocol": url.protocol,
                "country": proxy.get("country", "unknown"),
                "anonymity": proxy.get("anonymity", "unknown"),
                "times_failed": 0,
                "times_succeed": 0,
                "times_failed_in_row": 0
            }

            new_proxies.append(proxy)

        logger.debug(f"Adding {len(new_proxies)} proxies")
        self.proxies.extend(new_proxies)

        # Update index for new proxies
        for i, proxy in enumerate(new_proxies, start=start_index):
            self.index.add_proxy(i, proxy)

        self.update_data(remove_duplicates=True)

    def rm_proxy(self, index: int):
        if 0 <= index < len(self.proxies):
            # Remove from index first
            proxy = self.proxies[index]
            self.index.remove_proxy(index, proxy)

            # Remove from a list
            self.proxies.pop(index)

            # Update indices for remaining proxies
            self.index.rebuild_index(self.proxies)

            if self.last_proxy_index is not None and index < self.last_proxy_index:
                self.last_proxy_index -= 1
            self._write_data()
        else:
            raise IndexError("Proxy does not exist")

    def rm_all_proxies(self):
        self.proxies = []
        self.index.clear()
        self._write_data()

    def get_proxy(self, return_type: str = "url", protocol: Union[list[str], str, None] = None,
                  country: Union[list[str], str, None] = None,
                  anonymity: Union[list[str], str, None] = None,
                  exclude_protocol: Union[list[str], str, None] = None,
                  exclude_country: Union[list[str], str, None] = None,
                  exclude_anonymity: Union[list[str], str, None] = None) -> URL | None:

        if self.min_proxies and len(self.proxies) < self.min_proxies:
            raise NoProxyAvailable("Not enough proxies available. Fetch more proxies.")

        valid_indices = set(range(len(self.proxies)))

        # Include filters
        if protocol:
            protocol = [protocol] if isinstance(protocol, str) else protocol
            protocol_indices = set().union(*(self.index.protocol_index[p] for p in protocol))
            valid_indices &= protocol_indices

        if country:
            country = [country] if isinstance(country, str) else country
            country_indices = set().union(*(self.index.country_index[c] for c in country))
            valid_indices &= country_indices

        if anonymity:
            anonymity = [anonymity] if isinstance(anonymity, str) else anonymity
            anonymity_indices = set().union(*(self.index.anonymity_index[a] for a in anonymity))
            valid_indices &= anonymity_indices

        # Exclude filters
        if exclude_protocol:
            exclude_protocol = [exclude_protocol] if isinstance(exclude_protocol, str) else exclude_protocol
            exclude_indices = set().union(*(self.index.protocol_index[p] for p in exclude_protocol))
            valid_indices -= exclude_indices

        if exclude_country:
            exclude_country = [exclude_country] if isinstance(exclude_country, str) else exclude_country
            exclude_indices = set().union(*(self.index.country_index[c] for c in exclude_country))
            valid_indices -= exclude_indices

        if exclude_anonymity:
            exclude_anonymity = [exclude_anonymity] if isinstance(exclude_anonymity, str) else exclude_anonymity
            exclude_indices = set().union(*(self.index.anonymity_index[a] for a in exclude_anonymity))
            valid_indices -= exclude_indices

        if not valid_indices:
            raise NoProxyAvailable("No proxy found with the given parameters.")

        # Avoid consecutive same proxy unless it's the only option
        if (
                self.last_proxy_index is not None
                and self.last_proxy_index in valid_indices
                and len(valid_indices) > 1
        ):
            valid_indices.remove(self.last_proxy_index)

        selected_index = choice(list(valid_indices))
        self.last_proxy_index = selected_index
        chosen_proxy = self.proxies[selected_index][return_type]
        logger.debug(f"Chosen proxy: {chosen_proxy}")
        return chosen_proxy

    def __len__(self):
        return len(self.proxies)
