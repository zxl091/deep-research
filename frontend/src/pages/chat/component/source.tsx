import styles from './source.module.scss'
import { sourceLink } from '@/utils/source-link'

export default function Source(props: {
  list: API.ChatItem['search_results']
}) {
  const { list } = props

  return (
    <div className={styles['source__list']}>
      {list?.map((source) => (
        <a
          className={styles['source__item']}
          key={source.id}
          href={sourceLink(source.url)}
          target={source.url?.startsWith('local://') ? '_self' : '_blank'}
        >
          <img className={styles['icon']} src={source.siteIcon} />
          <div className={styles['info']}>
            <span className={styles['host']}>{source.host}</span>
            <span className={styles['name']} title={source.name}>
              {source.name}
            </span>
          </div>
        </a>
      ))}
    </div>
  )
}
