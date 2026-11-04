import { computed } from 'vue'
import {
  NAV_GROUPS,
  filterNavGroups,
  getMoreNavItems,
  getPrimaryNavItems,
} from '@/config/nav'
import { useAuthStore } from '@/stores/auth'
import { useNavOrderStore } from '@/stores/nav-order'

export function useOrderedPrimaryNav() {
  const auth = useAuthStore()
  const navOrder = useNavOrderStore()

  const groups = computed(() =>
    filterNavGroups(NAV_GROUPS, auth.isAdmin, auth.menus),
  )

  const items = computed(() =>
    navOrder.sortItems(getPrimaryNavItems(groups.value)),
  )

  const moreItems = computed(() => getMoreNavItems(groups.value))

  function commitOrder(hrefs: string[]) {
    navOrder.setVisibleOrder(hrefs)
  }

  return { items, moreItems, groups, commitOrder }
}
