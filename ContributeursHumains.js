/* global mw */
( function () {
	'use strict';

	var ARTICLE_SUMMARY_ID = 'contributeurs-humains-resume';
	var HISTORY_INTRO_ID = 'contributeurs-humains-historique';
	var CACHE_VERSION = 'v2';
	var TOOLFORGE_API_BASE = 'https://wikifame.toolforge.org';
	var REQUEST_TIMEOUT_MS = 8000;
	var PENDING_RETRY_DELAYS_MS = [ 3000, 10000 ];
	var numberFormatter = new Intl.NumberFormat( 'fr-FR' );
	var percentageFormatter = new Intl.NumberFormat( 'fr-FR', {
		maximumFractionDigits: 1,
		style: 'percent'
	} );
	var config = mw.config.get( [
		'wgAction',
		'wgArticleId',
		'wgCurRevisionId',
		'wgDiffNewId',
		'wgDiffOldId',
		'wgNamespaceNumber',
		'wgPageName',
		'wgRevisionId'
	] );

	if ( config.wgNamespaceNumber !== 0 || !config.wgArticleId ) {
		return;
	}

	mw.loader.using( [ 'mediawiki.util' ] ).then( function () {
		if ( config.wgAction === 'history' ) {
			addHistoryIntroduction();
			return;
		}

		if (
			config.wgAction === 'view' &&
			!config.wgDiffOldId &&
			!config.wgDiffNewId &&
			config.wgRevisionId === config.wgCurRevisionId
		) {
			addArticleSummary();
		}
	} ).catch( function ( error ) {
		mw.log.error( 'ContributeursHumains : initialisation impossible', error );
	} );

	function insertBelowSubtitle( element ) {
		var siteSub = document.getElementById( 'siteSub' );
		var vectorSlot = document.querySelector( '.vector-body-before-content' );
		var bodyContent = document.getElementById( 'bodyContent' );

		if ( siteSub ) {
			siteSub.after( element );
			return true;
		}

		if ( vectorSlot ) {
			vectorSlot.prepend( element );
			return true;
		}

		if ( bodyContent ) {
			bodyContent.prepend( element );
			return true;
		}

		return false;
	}

	function addHistoryIntroduction() {
		var box;
		var line1;
		var line2;
		var line3;

		if ( document.getElementById( HISTORY_INTRO_ID ) ) {
			return;
		}

		box = document.createElement( 'div' );
		box.id = HISTORY_INTRO_ID;
		box.className = 'contributeurs-humains contributeurs-humains--historique';
		box.setAttribute( 'role', 'note' );

		line1 = document.createElement( 'p' );
		line1.textContent = 'Chaque ligne correspond à une version de l’article et indique qui l’a modifiée.';

		line2 = document.createElement( 'p' );
		line2.append( document.createTextNode( 'Pour commencer, consultez ' ) );
		line2.append( createWikiLink(
			'Aide:Comment modifier une page',
			'l’aide à la modification'
		) );
		line2.append( document.createTextNode( ' ou entraînez-vous dans ' ) );
		line2.append( createWikiLink(
			'Wikipédia:Bac à sable',
			'le bac à sable'
		) );
		line2.append( document.createTextNode( '.' ) );

		line3 = document.createElement( 'p' );
		line3.append( document.createTextNode( 'Vous pouvez aussi ' ) );
		line3.append( createEditLink() );
		line3.append( document.createTextNode( '.' ) );

		box.append( line1, line2, line3 );
		insertBelowSubtitle( box );
	}

	async function addArticleSummary() {
		var cacheKey = getCacheKey();
		var cached = readCache( cacheKey );
		var data;
		var summary;

		if ( document.getElementById( ARTICLE_SUMMARY_ID ) ) {
			return;
		}

		try {
			data = cached || await loadContributionData();
			if ( !data ) {
				return;
			}
			if ( !cached ) {
				writeCache( cacheKey, data );
			}
			summary = buildArticleSummary( data );
			if ( summary ) {
				insertBelowSubtitle( summary );
			}
		} catch ( error ) {
			mw.log.warn( 'ContributeursHumains : données indisponibles', error );
		}
	}

	async function loadContributionData() {
		var url = TOOLFORGE_API_BASE + '/v1/frwiki/pages/' +
			encodeURIComponent( config.wgArticleId ) +
			'?revision_id=' + encodeURIComponent( config.wgCurRevisionId );
		var attempt;
		var data;

		for ( attempt = 0; attempt <= PENDING_RETRY_DELAYS_MS.length; attempt++ ) {
			data = await fetchJson( url, {
				credentials: 'omit',
				referrerPolicy: 'no-referrer'
			} );

			if ( data.status === 'ready' ) {
				if (
					typeof data.distinct_contributors !== 'number' ||
					!Array.isArray( data.contributors )
				) {
					throw new Error( 'Réponse d’attribution invalide.' );
				}

				return normalizeContributionData( data );
			}

			if ( data.status !== 'pending' ) {
				throw new Error( 'État d’attribution inconnu.' );
			}

			if ( attempt < PENDING_RETRY_DELAYS_MS.length ) {
				await wait( PENDING_RETRY_DELAYS_MS[ attempt ] );
			}
		}

		return null;
	}

	function normalizeContributionData( data ) {
		return {
			humanCount: data.distinct_contributors,
			limited: Boolean( data.count_limited ),
			topEditors: data.contributors.slice( 0, 3 ).map( function ( editor ) {
				return {
					share: Number( editor.share ),
					username: editor.username
				};
			} )
		};
	}

	function wait( delayMs ) {
		return new Promise( function ( resolve ) {
			window.setTimeout( resolve, delayMs );
		} );
	}

	async function fetchJson( url, options ) {
		var controller = new AbortController();
		var timeout = window.setTimeout( function () {
			controller.abort();
		}, REQUEST_TIMEOUT_MS );
		var response;

		try {
			response = await fetch( url, Object.assign( {
				headers: {
					Accept: 'application/json'
				},
				signal: controller.signal
			}, options ) );

			if ( !response.ok ) {
				throw new Error( 'Erreur HTTP ' + response.status );
			}

			return await response.json();
		} finally {
			window.clearTimeout( timeout );
		}
	}

	function buildArticleSummary( data ) {
		var box = document.createElement( 'div' );
		var topEditors = data.topEditors;
		var otherCount = Math.max( 0, data.humanCount - topEditors.length );

		if ( data.humanCount < 1 ) {
			return null;
		}

		box.id = ARTICLE_SUMMARY_ID;
		box.className = 'contributeurs-humains contributeurs-humains--article';
		box.setAttribute( 'role', 'note' );
		box.title = 'Principaux contributeurs du texte actuellement visible, selon WikiWho.';

		box.append( document.createTextNode( 'Article rédigé par ' ) );

		if ( topEditors.length ) {
			appendEditorList( box, topEditors, otherCount > 0 );
			if ( otherCount > 0 ) {
				box.append( document.createTextNode( ' et ' ) );
				box.append( createHistoryCountLink( otherCount, data.limited, true ) );
			}
		} else {
			box.append( createHistoryCountLink( data.humanCount, data.limited, false ) );
		}

		box.append( document.createTextNode( '.' ) );
		return box;
	}

	function appendEditorList( parent, editors, followedByOthers ) {
		editors.forEach( function ( editor, index ) {
			if ( index > 0 ) {
				parent.append( document.createTextNode(
					!followedByOthers && index === editors.length - 1 ? ' et ' : ', '
				) );
			}
			parent.append( createEditorLink( editor ) );
		} );
	}

	function createEditorLink( editor ) {
		var link = document.createElement( 'a' );

		link.href = mw.util.getUrl( 'Utilisateur:' + editor.username );
		link.textContent = editor.username.replace( /_/g, ' ' );
		link.title = 'Voir la page utilisateur de ' + editor.username.replace( /_/g, ' ' );
		if ( Number.isFinite( editor.share ) ) {
			link.title += ' — ' + percentageFormatter.format( editor.share ) +
				' des tokens actuellement visibles';
		}
		return link;
	}

	function createHistoryCountLink( count, limited, isRemainder ) {
		var link = document.createElement( 'a' );
		var personLabel = count === 1 ? 'personne' : 'personnes';
		link.href = mw.util.getUrl( config.wgPageName, { action: 'history' } );
		link.textContent = ( limited ? 'au moins ' : '' ) +
			numberFormatter.format( count ) + ' ' +
			( isRemainder ? ( count === 1 ? 'autre ' : 'autres ' ) : '' ) +
			personLabel;
		link.title = 'Voir l’historique complet de l’article';
		return link;
	}

	function createWikiLink( title, label ) {
		var link = document.createElement( 'a' );
		link.href = mw.util.getUrl( title );
		link.textContent = label;
		return link;
	}

	function createEditLink() {
		var link = document.createElement( 'a' );
		link.href = mw.util.getUrl( config.wgPageName, { veaction: 'edit' } );
		link.textContent = 'modifier directement cet article';
		return link;
	}

	function getCacheKey() {
		return 'contributeurs-humains:' + CACHE_VERSION + ':' +
			config.wgArticleId + ':' + config.wgCurRevisionId;
	}

	function readCache( key ) {
		var raw;

		try {
			raw = window.sessionStorage.getItem( key );
			return raw ? JSON.parse( raw ) : null;
		} catch ( error ) {
			return null;
		}
	}

	function writeCache( key, data ) {
		try {
			window.sessionStorage.setItem( key, JSON.stringify( data ) );
		} catch ( error ) {
			// Le gadget reste fonctionnel lorsque le stockage est désactivé ou plein.
		}
	}
}() );
