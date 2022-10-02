/* global $, ExpandingTextarea */
/* 
 This handles a vote, either in edit or interact mode. The JS object takes
 control of the DOM under the provided element `elem`; no other object should
 touch the DOM here. Currently this maintains the DOM and the internal data
 structure to the same value, redundantly; I don't love this, but I can't think
 of a much better way.
 */
var DisplayVote = (function (args) {
  /* args:
  * elem: a jQuery selector for a single empty element
  * edit: whether editing this vote (used only for posting new votes)
  * vote: vote data if pre-populated
  * active: whether voting is currently ongoing
  * (at most one of edit or active can be true)
  * socket: a SocketIO object used to emit vote messages
  * channel_id: the channel_id for the chapter
  */
  let edit_mode = args.edit !== undefined ? args.edit : false;
  let vote_active = args.active !== undefined ? args.active : false;
  if (edit_mode && vote_active) {
    throw 'Vote cannot be both editing and active';
  }

  let $el = args.elem;

  let vote_obj = args.vote !== undefined ? args.vote :
      { question: '', multivote: true, writein_allowed: true,
        votes_hidden: false, votes: [] };

  let rv = {
    _edit_mode: edit_mode,
    _vote: { ...vote_obj },

    length: vote_obj.votes.length,
    socket: args.socket !== undefined ? args.socket : null,
    channel_id: args.channel_id !== undefined ? args.channel_id : null,

    // this function takes all the vote entry divs in the DOM and sets their
    // data-index attributes to what they should be
    // it should be called after any manipulation of the DOM
    _reindex_dom: function () {
      $el.find('div.real-vote').each(function (ind) {
        $(this).attr('data-index', ind);
      });
    },

    get_vote: function (ind, as_active=false) {
      let v = this._vote.votes[ind];
      if (v._vote_obj !== undefined) {
        return v._vote_obj;
      }
      let parent_obj = this;
      let $vel = $el.find('.real-vote').slice(ind, ind + 1);
      let rv;

      // this $te is a jQuery containing the textarea
      function end_edit ($te) {
        let text = $te.val();
        let $vtd = $te.closest('.vote-text');
        v.text = text;
        $te.closest('div').text(text).click(function () { start_edit($vtd); });
        $te.closest('div.real-vote').removeAttr('data-editing');
        if (as_active) {
          let vote_info = { ...v };
          delete vote_info._vote_obj;
          let msg = { channel: parent_obj.channel_id,
                      vote: parent_obj._vote.db_id,
                      vote_info: vote_info };
          parent_obj.socket.emit('new_vote_entry', msg);
          rv.del();
        }
      }
      
      // this $te is a jQuery containing the vote-text div
      function start_edit ($te) {
        let opt_text = $te.text();
        let new_elem = $('<textarea class="vote-editor" rows="1" data-min-rows="1"></textarea>').val(opt_text);
        new_elem.keydown(function (ev) {
          if (ev.which == 13 && !ev.shiftKey) {
            end_edit($(this));
            return false;
          } else {
            return true;
          }
        });
        $te.off('click').html(new_elem);
        $te.closest('div.real-vote').attr('data-editing', '1');
        ExpandingTextarea({ elem: new_elem, pixel_height: 28 });
        new_elem.focus();
      }

      rv = {
        info: v,
        voted_for: false,
        editing: function () {
          return ($vel.attr('data-editing') === '1');
        },
        toggle_edit: function () {
          if (!(edit_mode || as_active)) {
            return;
          }
          if (this.editing()) {
            end_edit($vel.find('textarea'));
          } else {
            start_edit($vel.find('.vote-text'));
          }
        },
        del: function () {
          if (!(edit_mode || as_active)) {
            return;
          }
          let ind = $vel.attr('data-index');
          parent_obj._vote.votes.splice(ind, 1);
          $vel.remove();
          parent_obj._reindex_dom();
        },
        set_count: function (count) {
          if (!vote_active) {
            return;
          }
          this.info.vote_count = count;
          $vel.find('.vote-count').text(count);
          parent_obj.reorder_votes();
        },
        set_voted: function (val) {
          this.voted_for = val;
          if (val) {
            $vel.addClass('voted-for');
          } else {
            $vel.removeClass('voted-for');
          }
        },
        toggle_voted_for: function () {
          if (!vote_active || parent_obj.socket === null) {
            return;
          }
          if (this.voted_for) {
            // unvote
            this.set_voted(false);
            let msg = { channel: parent_obj.channel_id,
                        vote: parent_obj._vote.db_id,
                        option: this.info.db_id };
            parent_obj.socket.emit('remove_vote', msg);
          } else {
            // add vote
            if (!parent_obj._vote.multivote) {
              $el.find('.real-vote').removeClass('voted-for');
            }
            this.set_voted(true);
            let msg = { channel: parent_obj.channel_id,
                        vote: parent_obj._vote.db_id,
                        option: this.info.db_id };
            parent_obj.socket.emit('add_vote', msg);
          }
        },
      };
      v._vote_obj = rv;
      return rv;
    },

    get_vote_dbid: function (dbid) {
      let ind = this._vote.votes.findIndex(function (el) {
        return el.db_id == dbid;
      });
      if (ind == -1) {
        return null;
      }
      return this.get_vote(ind);
    },

    add_new_vote: function (v, as_active=false) {
      let vdata = v !== undefined ? v : { text: '', killed: false, vote_count: 0 };
      let ind = this._vote.votes.length;

      function make_vote_el (vd) {
        let $new_entry = $('<div class="vote-entry real-vote"><div class="vote-text"></div></div>');
        $new_entry.attr('data-index', ind);
        if (vd.db_id !== undefined) {
          $new_entry.attr('data-dbid', vd.db_id);
        }
        $new_entry.append(`<div class="vote-count">${vd.vote_count}</div>`)
        $new_entry.append('<div class="delete-vote" title="Delete entry">✘</div>');
        $new_entry.find('.vote-text').text(vd.text);
        return $new_entry;
      }

      let new_entry = make_vote_el(vdata);
      let $nv = $el.find('div.new-vote');
      if ($nv.length > 0) {
        $nv.before(new_entry);
      } else {
        $el.find('.vote-entries').append(new_entry);
      }
      rv._vote.votes.push(vdata);
      let vo = this.get_vote(ind, as_active);
      /* the methods on vo check if edit_mode is set, so no need to make the
      handlers conditional */
      new_entry.find('div.vote-text').click(function () {
        if (edit_mode) {
          vo.toggle_edit();
        } else if (vote_active) {
          vo.toggle_voted_for();
        }
      });
      new_entry.find('div.delete-vote').click(function () {
        vo.del();
      });
      return vo;
    },

    get_vote_data: function () {
      // TODO make the question textarea work like the vote ones
      let question;
      if (this._edit_mode) {
        question = $el.find('.vote-question').find('textarea').val();
        console.log("editmode: question =", question);
      } else {
        question = $el.find('.vote-question').text();
        console.log("no editmode: question =", question);
      }
      this._vote.question = question;

      let rv = { ...this._vote };
      rv.votes = rv.votes.map(function (i) {
        let rv = { ...i };
        delete rv._vote_obj;
        return rv;
      });
      return rv;
    },

    // Reorder the vote entries, both in the internal representation
    // and the DOM, in descending order of vote count. Called every
    // time the vote updates for shown votes, and after the vote
    // closes and totals are revealed for hidden votes.
    reorder_votes: function () {
      this._vote.votes.sort((a, b) => b.vote_count - a.vote_count);
      let $ent = $el.find('.vote-entries');
      let ta = Array.from(this._vote.votes).reverse();
      for (let v of ta) {
        let $e = $ent.find(`[data-dbid="${v.db_id}"]`);
        $e.detach();
        $ent.prepend($e);
      }
      this._reindex_dom();
    },
  };

  // set up vote
  function make_start_el () {
    let rv = $('<div class="vote"></div>');
    if (edit_mode) {
      rv.addClass('vote-editing');
    }
    if (vote_active && vote_obj.writein_allowed) {
      rv.addClass('can-add-votes');
    }
    let vq = $('<div class="vote-question"></div>');
    if (edit_mode) {
      let te = $('<textarea class="vote-editor" rows="1" data-min-rows="1" placeholder="What are you voting on?"></textarea>');
      te.val(vote_obj.question);
      vq.append(te);
    } else {
      vq.text(vote_obj.question);
    }
    rv.append(vq);
    let ves = $('<div class="vote-entries"></div>');
    rv.append(ves);
    ves.append('<div class="vote-entry new-vote"><div class="vote-text">+ Add new option</div></div>');
    return rv;
  }
  $el.append(make_start_el());
  rv._vote.votes = [];
  for (let vote of vote_obj.votes) {
    rv.add_new_vote(vote);
  }

  if (edit_mode) {
    ExpandingTextarea({ elem: $el.find('textarea.vote-editor'), pixel_height: 28 });
  }

  $el.find('div.new-vote').click(function () {
    if (edit_mode) {
      let vo = rv.add_new_vote();
      vo.toggle_edit();
    } else if (vote_active && rv._vote.writein_allowed) {
      let vo = rv.add_new_vote(undefined, true);
      vo.toggle_edit();
    }
  });

  return rv;
});
