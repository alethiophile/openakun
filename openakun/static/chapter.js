/* global $, moment, is_author, chapter_id, csrf_token, Quill,
   fix_quill_html, post_url, DisplayVote, channel_id, vote_data_list,
   nunjucks, anon_username, make_random_token, ExpandingTextarea,
   io */
$(function () {
  $('.rerender-date').each(function () {
    var m = moment($(this).data('dateval'));
    var rd = m.format("MMM Do, YYYY h:mm A");
    $(this).html(rd);
  });
  if (is_author !== undefined) {
    var quill = new Quill('#quill-editor', {
      theme: 'snow',
    });
    $('#post-button').click(function () {
      let post_type = $('input:radio[name=post_type]:checked').val();
      let new_chapter = $('#newchapter').is(':checked');
      let chap_title = $('#chaptertitle_inp').val();
      if (new_chapter && chap_title === '') {
        alert("Chapter title can't be empty");
        return false;
      }
      let post_data = { chapter_id: chapter_id,
                        new_chapter: new_chapter, chapter_title: chap_title,
                        _csrf_token: csrf_token };
      if (post_type == 'text') {
        let post_html = quill.root.innerHTML;
        post_html = fix_quill_html(post_html);
        post_data['post_text'] = post_html;
        // this names one of the server-side PostType enum values
        post_data['post_type'] = 'Text';
      }
      else if (post_type == 'vote') {
        /* let vote_question = $('#vote-editor textarea.vote-editor').val();
         * let vote_entries = [];
         * $('div.vote-entries div.vote-text').each(function () {
         *   vote_entries.push($(this).html());
         * });*/
        /* post_data['vote_question'] = vote_question;
         * post_data['vote_options'] = vote_entries;*/
        post_data['vote_data'] = vote_editor.get_vote_data();
        post_data['post_type'] = 'Vote';
        console.log(post_data);
      }
      $.ajax(post_url, {
        method: 'POST',
        data: JSON.stringify(post_data),
        contentType: 'application/json',
        /* success: function () {
         *   document.location.href = document.location.href;
         * },*/
        error: function (jqxhr, status, errorThrown) {
          var errstr = "Error: " + status + " " + errorThrown;
          console.log(errstr);
          alert(errstr);
        },
      });
    });
    let show_correct_editor = (function () {
      let val = $('input:radio[name=post_type]:checked').val();
      if (val === 'text') {
        $('#text-editor').show();
        $('#vote-editor').hide();
      } else if (val === 'vote') {
        $('#vote-editor').show();
        $('#text-editor').hide();
      } else if (val === 'writein') {
        
      }
    });
    // we call this at the start to handle the case where the user
    // reloads the page after switching editors; in this case the
    // browser will save the radio button position, so we want to show
    // the corresponding editor
    show_correct_editor();
    $('input:radio[name=post_type]').click(function () {
      show_correct_editor();
    });
  }

  var socket = io();
  var msgs_recvd = new Set();
  socket.on('connect', function () {
    console.log('joining room');
    socket.emit('join', { channel: channel_id }, function (res) {
      console.log("join result", res)
      $('.vote-from-server').each(function () {
        let vi = $(this).attr('data-id');
        let vd = vote_data_list[vi];
        console.log("vote-from-server handling", vd);
        if (vd.active) {
          socket.emit('get_my_votes', { vote: vd.db_id });
        }
      });
      socket.emit('backlog', { channel: channel_id });
    });
  });
  socket.on('chat_msg', function (data) {
    console.log('got chat message', data);
    data.rendered_date = moment(data.date).format("MMM Do, YYYY h:mm A");
    if (!msgs_recvd.has(data.id_token)) {
      add_chat_msg(data);
      msgs_recvd.add(data.id_token);
    }
  });
  var post_tmpl = nunjucks.compile($('#render_post').html());
  let active_votes = {};
  socket.on('new_post', function (data) {
    console.log('new post:', data);
    data.rendered_date = moment(data.date_millis).format("MMM Do, YYYY h:mm A");
    if (data.type == 'Text') {
      data.render_text = data.text;
      let $d = $(post_tmpl.render({ p: data }));
      $('#story-content').append($d);
    } else if (data.type == 'Vote') {
      data.render_text = '<div class="rt-vote"></div>';
      let $d = $(post_tmpl.render({ p: data }));
      let $vel = $d.find('.rt-vote');
      $('#story-content').append($d);
      let dv = DisplayVote({
        elem: $vel,
        edit: false,
        vote: data.vote_data,
        active: true,
        socket: socket,
        channel_id: channel_id
      });
      active_votes[data.vote_data.db_id] = dv;
    }
  });
  socket.on('option_vote_total', function (data) {
    let vote_id = data.vote;
    let dv = active_votes[vote_id];
    dv.get_vote_dbid(data.option).set_count(data.vote_total);
  });
  socket.on('vote_entry_added', function (data) {
    let vote_id = data.vote;
    let dv = active_votes[vote_id];
    dv.add_new_vote(data.vote_info);
  });
  socket.on('user_vote', function (data) {
    let vote_id = data.vote;
    let dv = active_votes[vote_id];
    dv.get_vote_dbid(data.option).set_voted(data.value);
  });
  socket.on('disconnect', function (reason) {
    console.log('disconnected', reason);
    socket.connect();
  });
  socket.on('connect_error', function (error) {
    console.log('connection error');
    setTimeout(function () {
      console.log('trying reconnect');
      socket.connect();
    }, 1000);
  });
  function socket_err(error) {
    console.log('error', error);
  }
  socket.on('error', socket_err);
  socket.on('connect_timeout', socket_err);

  var chat_tmpl = nunjucks.compile($('#render_chatmsg').html());
  /* Adds a message to the chatbox, rendering it. Called
     for every message that comes in over the wire. */
  function add_chat_msg (msg) {
    var $cm = $('#chat-messages');
    var scrollBottomVal = $cm[0].scrollHeight - $cm.height();
    if (msg.is_anon) {
      msg.username = anon_username;
    }
    var $d = $(chat_tmpl.render({ c: msg}));
    $('#chat-messages').append($d);
    var msgHeight = $d.height();
    if (scrollBottomVal - $cm[0].scrollTop < msgHeight) {
      $cm.scrollTop($cm.height());
    }
  }
  /* Send a chat message. Takes a string message, arranges
     for it to be sent. */
  function chat_send (msg) {
    var mo = { channel: channel_id, msg: msg, id_token: make_random_token() };
    socket.emit('chat_msg', mo);
  }
  function chat_trigger_send () {
    var $ct = $('#chat-type');
    var chat_msg = $ct.val();
    if (chat_msg === '') {
      return;
    }
    chat_send(chat_msg);
    $ct.val('');
    $ct.attr('rows', $ct.data('min-rows'));
  }

  ExpandingTextarea({
    id: 'chat-type',
    pixel_height: 21,
    before_resize: function () {
      let $cm = $('#chat-messages');
      this.$cm = $cm;
      this.scrollFrac = $cm[0].scrollTop / ($cm[0].scrollHeight - $cm.height());
    },
    after_resize: function () {
      let $cm = this.$cm;
      $cm[0].scrollTop = this.scrollFrac * ($cm[0].scrollHeight - $cm.height());
    },
  });

  $('#chat-type').keydown(function (ev) {
    if (ev.which == 13 && !ev.shiftKey) {
      chat_trigger_send();
      return false;
    }
    return true;
  });
  $('#chat-send').click(function () {
    chat_trigger_send();
  });
  $('#newchapter').change(function () {
    if ($(this).is(':checked')) {
      $('#chaptertitle_inp').css('visibility', 'visible');
    } else {
      $('#chaptertitle_inp').css('visibility', 'hidden');
    }
  });

  let $vedit = $('#vote-editor');
  let vote_editor;
  if ($vedit.length > 0) {
    vote_editor = DisplayVote({
      elem: $vedit,
      edit: true
    });
  }

  $('.vote-from-server').each(function () {
    let vi = $(this).attr('data-id');
    let vd = vote_data_list[vi];
    let dv = DisplayVote({
      elem: $(this),
      edit: false,
      vote: vd,
      active: vd.active,
      socket: socket,
      channel_id: channel_id
    });
    if (vd.active) {
      active_votes[vd.db_id] = dv;
    }
    console.log("vote-from-server handling", vd);
    if (vd.active) {
      socket.emit('get_my_votes', { vote: vd.db_id });
    }
  });
});
